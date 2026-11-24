# /// script
# requires-python = ">=3.12"
# ///
"""Paddock versioned multi-platform model configuration engine and CLI."""

from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any


def _import_memory():
    """Import `memory.py` from sibling `paddock_core/` or repo `core/memory/`."""
    here = Path(__file__).resolve().parent
    sibling = here / "memory.py"
    if sibling.is_file():
        existing = sys.modules.get("memory")
        if existing is not None and getattr(existing, "__file__", None):
            try:
                if Path(existing.__file__).resolve() == sibling.resolve():
                    return existing
            except OSError:
                pass
        if str(here) not in sys.path:
            sys.path.insert(0, str(here))
        import memory  # type: ignore[import-not-found]

        return memory

    repo_mem = here.parent / "memory" / "memory.py"
    if repo_mem.is_file():
        repo_root = here.parents[1]
        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))
        from core.memory import memory

        return memory

    raise ImportError("Unable to locate Paddock memory.py")


memory = _import_memory()

SCHEMA_VERSION = 1

PLATFORMS: tuple[str, ...] = ("antigravity", "claude", "codex", "copilot", "pi")
ROLES: tuple[str, ...] = ("generalist", "implementer", "ui", "planner", "builder", "verifier")
TIERS: tuple[str, ...] = ("fast", "workhorse", "flagship")
EFFORTS: tuple[str, ...] = ("low", "medium", "high")
DEFAULT_EFFORT = "medium"
DEFAULT_PRESET = "balanced"

ANTIGRAVITY_BARE_ALIASES: frozenset[str] = frozenset({"inherit", "flash_lite", "flash", "pro"})
TIER_TO_ANTIGRAVITY_ALIAS: dict[str, str] = {
    "fast": "flash",
    "workhorse": "pro",
    "flagship": "inherit",
}

DEFAULT_PROVIDERS: dict[str, str] = {
    "antigravity": "google",
    "claude": "anthropic",
    "codex": "openai",
    "copilot": "anthropic",
    "pi": "anthropic",
}

PROVIDER_CATALOGS: dict[str, dict[str, dict[str, dict[str, str | None]]]] = {
    "antigravity": {
        "google": {
            "fast": {"model": "gemini-4-hydrogen", "effort": "medium"},
            "workhorse": {"model": "gemini-4-neon", "effort": "medium"},
            "flagship": {"model": "gemini-4-carbon", "effort": "medium"},
        },
        "anthropic": {
            "fast": {"model": "gemini-4-hydrogen", "effort": "medium"},
            "workhorse": {"model": "claude-sonnet-5-5", "effort": "medium"},
            "flagship": {"model": "claude-opus-5-5", "effort": "medium"},
        },
        "native": {
            "fast": {"model": "flash", "effort": None},
            "workhorse": {"model": "pro", "effort": None},
            "flagship": {"model": "inherit", "effort": None},
        },
    },
    "claude": {
        "anthropic": {
            "fast": {"model": "haiku", "effort": "medium"},
            "workhorse": {"model": "sonnet", "effort": "medium"},
            "flagship": {"model": "opus", "effort": "medium"},
        },
    },
    "codex": {
        "openai": {
            "fast": {"model": "gpt-6-luna", "effort": "medium"},
            "workhorse": {"model": "gpt-6.2-sol", "effort": "medium"},
            "flagship": {"model": "gpt-6.2-astra", "effort": "medium"},
        },
    },
    "copilot": {
        "anthropic": {
            "fast": {"model": "claude-haiku-5.5", "effort": "medium"},
            "workhorse": {"model": "claude-sonnet-5.5", "effort": "medium"},
            "flagship": {"model": "claude-opus-5.5", "effort": "medium"},
        },
        "openai": {
            "fast": {"model": "gpt-6-luna", "effort": "medium"},
            "workhorse": {"model": "gpt-6.2-sol", "effort": "medium"},
            "flagship": {"model": "gpt-6.2-astra", "effort": "medium"},
        },
        "google": {
            "fast": {"model": "gemini-4-hydrogen", "effort": "medium"},
            "workhorse": {"model": "gemini-4-neon", "effort": "medium"},
            "flagship": {"model": "gemini-4-carbon", "effort": "medium"},
        },
    },
    "pi": {
        "anthropic": {
            "fast": {"model": "anthropic/claude-haiku-5-5", "effort": "medium"},
            "workhorse": {"model": "anthropic/claude-sonnet-5-5", "effort": "medium"},
            "flagship": {"model": "anthropic/claude-opus-5-5", "effort": "medium"},
        },
        "openai": {
            "fast": {"model": "openai/gpt-6-luna", "effort": "medium"},
            "workhorse": {"model": "openai/gpt-6.2-sol", "effort": "medium"},
            "flagship": {"model": "openai/gpt-6.2-astra", "effort": "medium"},
        },
        "google": {
            "fast": {"model": "google/gemini-4-hydrogen", "effort": "medium"},
            "workhorse": {"model": "google/gemini-4-neon", "effort": "medium"},
            "flagship": {"model": "google/gemini-4-carbon", "effort": "medium"},
        },
    },
}

PRESETS: dict[str, dict[str, tuple[str, str]]] = {
    "budget": {
        "generalist": ("fast", "medium"),
        "implementer": ("fast", "high"),
        "ui": ("workhorse", "low"),
        "planner": ("workhorse", "low"),
        "builder": ("workhorse", "low"),
        "verifier": ("fast", "high"),
    },
    "balanced": {
        "generalist": ("fast", "medium"),
        "implementer": ("workhorse", "medium"),
        "ui": ("workhorse", "medium"),
        "planner": ("workhorse", "medium"),
        "builder": ("workhorse", "medium"),
        "verifier": ("workhorse", "medium"),
    },
    "quality": {
        "generalist": ("fast", "medium"),
        "implementer": ("flagship", "medium"),
        "ui": ("workhorse", "high"),
        "planner": ("flagship", "medium"),
        "builder": ("workhorse", "medium"),
        "verifier": ("flagship", "medium"),
    },
}

SINGLE_PROVIDER_PLATFORMS = frozenset({"antigravity", "claude"})
MULTI_PROVIDER_PLATFORMS = frozenset({"codex", "copilot", "pi"})
SINGLE_MODEL_RE = re.compile(r"\A[a-z0-9][a-z0-9._-]{1,63}\Z")
MULTI_MODEL_RE = re.compile(r"\A[a-z0-9][a-z0-9._-]{0,63}(?:/[a-z0-9][a-z0-9._-]{0,63})?\Z")
ALLOWED_CONFIG_KEYS = frozenset({"schema_version", "preset", "tiers", "roles", "overrides"})
UNSAFE_MODEL_CHARS = frozenset("\n\r\t\0'\"\\$`!;|&<>(){}[]#:,?*~ ")


def _is_symlink_or_junction(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(callable(is_junction) and is_junction())


def _serialize_tier_entry(model: str, effort: str | None) -> dict[str, str]:
    entry: dict[str, str] = {"model": model}
    if effort is not None:
        entry["effort"] = effort
    return entry


def _catalog_for(platform: str, provider: str) -> dict[str, dict[str, str]]:
    catalog = PROVIDER_CATALOGS[platform][provider]
    return {
        tier: _serialize_tier_entry(str(spec["model"]), spec["effort"])
        for tier, spec in catalog.items()
    }


def _preset_roles_dict(preset: str) -> dict[str, str | dict[str, str]]:
    out: dict[str, str | dict[str, str]] = {}
    for role in ROLES:
        tier, effort = PRESETS[preset][role]
        if effort == DEFAULT_EFFORT:
            out[role] = tier
        else:
            out[role] = {"tier": tier, "effort": effort}
    return out


def default_tiers() -> dict[str, dict[str, dict[str, str]]]:
    """Return built-in default provider tier mappings across all platforms."""
    return {
        platform: _catalog_for(platform, DEFAULT_PROVIDERS[platform])
        for platform in PLATFORMS
    }


def default_config(preset: str = DEFAULT_PRESET) -> dict[str, Any]:
    """Return complete default schema_version=1 model configuration."""
    clean_preset = validate_preset(preset)
    return {
        "schema_version": SCHEMA_VERSION,
        "preset": clean_preset,
        "tiers": default_tiers(),
        "roles": _preset_roles_dict(clean_preset),
        "overrides": {platform: {} for platform in PLATFORMS},
    }


def validate_platform(platform: str) -> str:
    if not isinstance(platform, str) or platform not in PLATFORMS:
        raise ValueError(f"Unknown platform {platform!r}; expected one of {PLATFORMS}")
    return platform


def validate_role(role: str) -> str:
    if not isinstance(role, str) or role not in ROLES:
        raise ValueError(f"Unknown role {role!r}; expected one of {ROLES}")
    return role


def validate_tier(tier: str) -> str:
    if not isinstance(tier, str) or tier not in TIERS:
        raise ValueError(f"Unknown tier {tier!r}; expected one of {TIERS}")
    return tier


def validate_preset(preset: str) -> str:
    if not isinstance(preset, str) or preset not in PRESETS:
        raise ValueError(f"Unknown preset {preset!r}; expected one of {tuple(PRESETS)}")
    return preset


def validate_effort(effort: str) -> str:
    if not isinstance(effort, str) or effort not in EFFORTS:
        raise ValueError(f"Unknown effort {effort!r}; expected one of {EFFORTS}")
    return effort


def validate_provider(platform: str, provider: str) -> str:
    clean_platform = validate_platform(platform)
    allowed = PROVIDER_CATALOGS[clean_platform]
    if not isinstance(provider, str) or provider not in allowed:
        raise ValueError(
            f"Unknown provider {provider!r} for platform {clean_platform!r}; "
            f"expected one of {tuple(allowed)}"
        )
    return provider


def is_antigravity_bare_alias(platform: str, model: str) -> bool:
    return platform == "antigravity" and model in ANTIGRAVITY_BARE_ALIASES


def validate_model_id(
    platform: str,
    model: str,
    effort: str | None = None,
) -> tuple[str, str | None]:
    """Validate model string and optional effort for `platform`."""
    clean_platform = validate_platform(platform)
    if not isinstance(model, str) or not model:
        raise ValueError("Model must be a non-empty string")
    if any(ch in UNSAFE_MODEL_CHARS for ch in model):
        raise ValueError(f"Unsafe or unsupported characters in model {model!r}")
    if ".." in model:
        raise ValueError(f"Path traversal sequence '..' rejected in model {model!r}")
    if model.startswith("/") or model.endswith("/") or model.count("/") > 1:
        raise ValueError(f"Invalid slash placement in model {model!r}")

    if clean_platform in SINGLE_PROVIDER_PLATFORMS:
        if not SINGLE_MODEL_RE.fullmatch(model):
            raise ValueError(
                f"Invalid model {model!r} for platform {clean_platform!r}; "
                "must match ^[a-z0-9][a-z0-9._-]{1,63}$"
            )
    else:
        if not (2 <= len(model) <= 128) or not MULTI_MODEL_RE.fullmatch(model):
            raise ValueError(
                f"Invalid model {model!r} for platform {clean_platform!r}; "
                "must be 2-128 chars matching ^[a-z0-9][a-z0-9._-]{0,63}(?:/[a-z0-9][a-z0-9._-]{0,63})?$"
            )

    clean_effort: str | None = None
    if effort is not None:
        clean_effort = validate_effort(effort)
        if is_antigravity_bare_alias(clean_platform, model):
            raise ValueError(
                f"Explicit effort {clean_effort!r} is not allowed on bare Antigravity alias {model!r}"
            )
    return model, clean_effort


def _validate_role_entry(role: str, entry: Any) -> str | dict[str, str]:
    validate_role(role)
    if isinstance(entry, str):
        return validate_tier(entry)
    if isinstance(entry, dict):
        unknown = set(entry) - {"tier", "effort"}
        if unknown or "tier" not in entry:
            raise ValueError(f"Invalid role entry for {role!r}: {entry!r}")
        tier = validate_tier(entry["tier"])
        if "effort" in entry and entry["effort"] is not None:
            effort = validate_effort(entry["effort"])
            return {"tier": tier, "effort": effort}
        return tier
    raise ValueError(f"Invalid role entry type for {role!r}: {entry!r}")


def _validate_tier_entry(platform: str, tier: str, entry: Any) -> dict[str, str]:
    validate_tier(tier)
    if not isinstance(entry, dict):
        raise ValueError(f"Tier entry {platform}.{tier} must be a mapping")
    unknown = set(entry) - {"model", "effort"}
    if unknown or "model" not in entry:
        raise ValueError(f"Invalid tier entry {platform}.{tier}: {entry!r}")
    model, effort = validate_model_id(platform, entry["model"], entry.get("effort"))
    return _serialize_tier_entry(model, effort)


def _validate_override_entry(platform: str, role: str, entry: Any) -> dict[str, str]:
    validate_role(role)
    if not isinstance(entry, dict) or not entry:
        raise ValueError(f"Override entry {platform}.{role} must be a non-empty mapping")
    unknown = set(entry) - {"model", "effort"}
    if unknown:
        raise ValueError(f"Unknown keys in override {platform}.{role}: {sorted(unknown)}")
    model = entry.get("model")
    effort = entry.get("effort")
    if model is not None:
        clean_model, clean_effort = validate_model_id(platform, model, effort)
        out: dict[str, str] = {"model": clean_model}
        if clean_effort is not None:
            out["effort"] = clean_effort
        return out
    if effort is None:
        raise ValueError(f"Override entry {platform}.{role} must set 'model' or 'effort'")
    return {"effort": validate_effort(effort)}


def validate_config(data: Any) -> dict[str, Any]:
    """Validate parsed `models.json` mapping against schema_version=1."""
    if not isinstance(data, dict):
        raise ValueError("Config root must be a JSON object")
    unknown_top = set(data) - ALLOWED_CONFIG_KEYS
    if unknown_top:
        raise ValueError(f"Unknown top-level config keys: {sorted(unknown_top)}")

    sv = data.get("schema_version")
    if type(sv) is not int or sv != SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported schema_version {sv!r}; expected integer {SCHEMA_VERSION}"
        )

    cleaned: dict[str, Any] = {"schema_version": SCHEMA_VERSION}

    if "preset" in data:
        cleaned["preset"] = validate_preset(data["preset"])

    if "tiers" in data:
        raw_tiers = data["tiers"]
        if not isinstance(raw_tiers, dict):
            raise ValueError("'tiers' must be a JSON object")
        clean_tiers: dict[str, dict[str, dict[str, str]]] = {}
        for plat, tier_map in raw_tiers.items():
            clean_plat = validate_platform(plat)
            if not isinstance(tier_map, dict):
                raise ValueError(f"'tiers.{clean_plat}' must be a JSON object")
            clean_tiers[clean_plat] = {
                validate_tier(t): _validate_tier_entry(clean_plat, t, spec)
                for t, spec in tier_map.items()
            }
        cleaned["tiers"] = clean_tiers

    if "roles" in data:
        raw_roles = data["roles"]
        if not isinstance(raw_roles, dict):
            raise ValueError("'roles' must be a JSON object")
        cleaned["roles"] = {
            validate_role(r): _validate_role_entry(r, val)
            for r, val in raw_roles.items()
        }

    if "overrides" in data:
        raw_overrides = data["overrides"]
        if not isinstance(raw_overrides, dict):
            raise ValueError("'overrides' must be a JSON object")
        clean_overrides: dict[str, dict[str, dict[str, str]]] = {}
        for plat, role_map in raw_overrides.items():
            clean_plat = validate_platform(plat)
            if not isinstance(role_map, dict):
                raise ValueError(f"'overrides.{clean_plat}' must be a JSON object")
            clean_overrides[clean_plat] = {
                validate_role(r): _validate_override_entry(clean_plat, r, spec)
                for r, spec in role_map.items()
            }
        cleaned["overrides"] = clean_overrides

    return cleaned


def _resolve_paddock_home(home: str | Path | None = None) -> Path:
    if home is not None:
        raw_home = Path(home).expanduser()
        if _is_symlink_or_junction(raw_home):
            raise ValueError(f"Refusing symlinked Paddock home directory: {raw_home}")
        return raw_home.resolve()

    env_home = os.environ.get("PADDOCK_HOME", "").strip()
    if env_home:
        raw_env = Path(env_home).expanduser()
        if _is_symlink_or_junction(raw_env):
            raise ValueError(f"Refusing symlinked PADDOCK_HOME directory: {raw_env}")
    else:
        default_raw = Path.home() / ".paddock"
        if _is_symlink_or_junction(default_raw) and memory.paddock_home() == default_raw.resolve():
            raise ValueError(f"Refusing symlinked Paddock home directory: {default_raw}")
    return memory.paddock_home()


def _validate_slug(slug: str) -> str:
    if (
        not isinstance(slug, str)
        or not slug
        or slug in {".", ".."}
        or "/" in slug
        or "\\" in slug
        or ".." in slug
    ):
        raise ValueError(f"Invalid repository slug for config path: {slug!r}")
    return slug


def config_path(
    scope: str,
    cwd: str | Path = ".",
    *,
    home: str | Path | None = None,
) -> Path:
    """Return validated config file path for `global` or `project` scope."""
    if scope not in ("global", "project"):
        raise ValueError(f"Invalid config scope {scope!r}; expected 'global' or 'project'")

    paddock_root = _resolve_paddock_home(home)
    if scope == "global":
        target = paddock_root / "models.json"
        if _is_symlink_or_junction(target):
            raise ValueError(f"Refusing symlinked config file: {target}")
        resolved = target.resolve()
        if resolved.parent != paddock_root or resolved.name != "models.json":
            raise ValueError(f"Resolved config path escapes Paddock home: {resolved}")
        return resolved

    slug = _validate_slug(memory.repo_slug(cwd))
    projects_dir = paddock_root / "projects"
    proj_dir = projects_dir / slug
    target = proj_dir / "models.json"
    for comp in (projects_dir, proj_dir, target):
        if _is_symlink_or_junction(comp):
            raise ValueError(f"Refusing symlinked config path component: {comp}")

    resolved_projects = projects_dir.resolve()
    resolved_proj = proj_dir.resolve()
    resolved_target = target.resolve()
    if resolved_projects.parent != paddock_root or resolved_proj.parent != resolved_projects:
        raise ValueError(f"Resolved project directory escapes Paddock home: {resolved_proj}")
    if resolved_target.parent != resolved_proj or resolved_target.name != "models.json":
        raise ValueError(f"Resolved project config escapes project directory: {resolved_target}")
    return resolved_target


def _read_validated_file(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    if path.is_dir():
        raise ValueError(f"Config path is a directory, expected file: {path}")
    text = path.read_text(encoding="utf-8")
    parsed = json.loads(text)
    return validate_config(parsed)


def load_config_layer(
    scope: str,
    cwd: str | Path = ".",
    *,
    home: str | Path | None = None,
    strict: bool = False,
) -> dict[str, Any] | None:
    """Load `global` or `project` models.json; fail-open with stderr warning unless `strict=True`."""
    try:
        path = config_path(scope, cwd, home=home)
        return _read_validated_file(path)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        if strict:
            raise
        print(
            f"paddock-models warning: ignoring invalid {scope} config: {exc}",
            file=sys.stderr,
        )
        return None


def _merge_tiers(
    global_cfg: dict[str, Any] | None,
    project_cfg: dict[str, Any] | None,
) -> dict[str, dict[str, dict[str, str]]]:
    effective = default_tiers()
    for cfg in (global_cfg, project_cfg):
        if not cfg:
            continue
        tiers_map = cfg.get("tiers")
        if not isinstance(tiers_map, dict):
            continue
        for plat, plat_tiers in tiers_map.items():
            for tier, spec in plat_tiers.items():
                effective[plat][tier] = dict(spec)
    return effective


def _layer_role_spec(cfg: dict[str, Any] | None, role: str) -> tuple[str, str | None] | None:
    if not cfg:
        return None
    roles_map = cfg.get("roles")
    if isinstance(roles_map, dict) and role in roles_map:
        entry = roles_map[role]
        if isinstance(entry, str):
            return entry, None
        if isinstance(entry, dict):
            return entry["tier"], entry.get("effort")
    preset_name = cfg.get("preset")
    if isinstance(preset_name, str) and preset_name in PRESETS:
        tier, effort = PRESETS[preset_name][role]
        return tier, (None if effort == DEFAULT_EFFORT else effort)
    return None


def _infer_tier_for_antigravity_model(
    model: str,
    effective_agy_tiers: dict[str, dict[str, str]],
    fallback_tier: str,
) -> str:
    for tier in TIERS:
        if effective_agy_tiers.get(tier, {}).get("model") == model:
            return tier
    for catalog in PROVIDER_CATALOGS["antigravity"].values():
        for tier, spec in catalog.items():
            if spec["model"] == model:
                return tier
    return fallback_tier


def antigravity_subagent_alias(model: str | None = None, tier: str | None = None) -> str:
    """Map concrete Antigravity model/tier to safe `invoke_subagent` / `run_workflow` enum."""
    if isinstance(model, str) and model in ANTIGRAVITY_BARE_ALIASES:
        return model
    if isinstance(tier, str) and tier in TIER_TO_ANTIGRAVITY_ALIAS:
        return TIER_TO_ANTIGRAVITY_ALIAS[tier]
    if isinstance(model, str):
        for catalog in PROVIDER_CATALOGS["antigravity"].values():
            for cat_tier, spec in catalog.items():
                if spec["model"] == model:
                    return TIER_TO_ANTIGRAVITY_ALIAS[cat_tier]
    return "pro"


def format_antigravity_swarm_model(model: str, effort: str | None) -> str:
    """Format Antigravity `swarm` model string (`<model>-<effort>` or bare alias verbatim)."""
    if model in ANTIGRAVITY_BARE_ALIASES:
        return model
    return f"{model}-{effort or DEFAULT_EFFORT}"


def load_effective_config(
    cwd: str | Path = ".",
    *,
    home: str | Path | None = None,
) -> dict[str, Any]:
    """Merge default <- global <- project configuration (used by profile inspection/apply)."""
    global_cfg = load_config_layer("global", cwd, home=home)
    project_cfg = load_config_layer("project", cwd, home=home)

    merged = default_config()
    merged["tiers"] = _merge_tiers(global_cfg, project_cfg)

    for layer_cfg in (global_cfg, project_cfg):
        if not layer_cfg:
            continue
        if "preset" in layer_cfg:
            merged["preset"] = layer_cfg["preset"]
            merged["roles"] = _preset_roles_dict(layer_cfg["preset"])
        if isinstance(layer_cfg.get("roles"), dict):
            for role, spec in layer_cfg["roles"].items():
                merged["roles"][role] = copy.deepcopy(spec)
        if isinstance(layer_cfg.get("overrides"), dict):
            for plat, role_map in layer_cfg["overrides"].items():
                for role, ov in role_map.items():
                    merged["overrides"].setdefault(plat, {})[role] = dict(ov)

    return merged


def _resolve_tier_baseline(
    platform: str,
    role: str,
    effective_tiers: dict[str, dict[str, dict[str, str]]],
    global_cfg: dict[str, Any] | None,
    project_cfg: dict[str, Any] | None,
) -> tuple[str, str, str, str | None]:
    proj_spec = _layer_role_spec(project_cfg, role)
    if proj_spec is not None:
        tier_source = "project-tier"
        tier, role_effort = proj_spec
    else:
        glob_spec = _layer_role_spec(global_cfg, role)
        if glob_spec is not None:
            tier_source = "global-tier"
            tier, role_effort = glob_spec
        else:
            tier_source = "default"
            tier, preset_eff = PRESETS[DEFAULT_PRESET][role]
            role_effort = None if preset_eff == DEFAULT_EFFORT else preset_eff

    tier_entry = effective_tiers[platform][tier]
    model = tier_entry["model"]
    if is_antigravity_bare_alias(platform, model):
        effort: str | None = None
    else:
        effort = role_effort if role_effort is not None else tier_entry.get("effort", DEFAULT_EFFORT)
    return tier_source, tier, model, effort


def _apply_override_layer(
    platform: str,
    ov: dict[str, str],
    fallback_model: str,
    fallback_effort: str | None,
) -> tuple[str, str | None]:
    model = ov.get("model", fallback_model)
    if is_antigravity_bare_alias(platform, model):
        return model, None
    ov_effort = ov.get("effort")
    if ov_effort is not None:
        return model, ov_effort
    return model, (fallback_effort if fallback_effort is not None else DEFAULT_EFFORT)


def resolve_role(
    platform: str,
    role: str,
    cwd: str | Path = ".",
    *,
    call_time_model: str | None = None,
    call_time_effort: str | None = None,
    global_cfg: dict[str, Any] | None = None,
    project_cfg: dict[str, Any] | None = None,
    home: str | Path | None = None,
    _layers_loaded: bool = False,
) -> dict[str, Any]:
    """Resolve effective model, effort, source layer, and Antigravity aliases for `(platform, role)`."""
    clean_platform = validate_platform(platform)
    clean_role = validate_role(role)

    if call_time_model is not None:
        validate_model_id(clean_platform, call_time_model, call_time_effort)
    elif call_time_effort is not None:
        validate_effort(call_time_effort)

    if not _layers_loaded:
        if global_cfg is None:
            global_cfg = load_config_layer("global", cwd, home=home)
        if project_cfg is None:
            project_cfg = load_config_layer("project", cwd, home=home)

    effective_tiers = _merge_tiers(global_cfg, project_cfg)
    tier_source, base_tier, base_model, base_effort = _resolve_tier_baseline(
        clean_platform,
        clean_role,
        effective_tiers,
        global_cfg,
        project_cfg,
    )

    proj_ov = (project_cfg or {}).get("overrides", {}).get(clean_platform, {}).get(clean_role)
    glob_ov = (global_cfg or {}).get("overrides", {}).get(clean_platform, {}).get(clean_role)

    resolved_tier: str | None = None
    if call_time_model is not None or call_time_effort is not None:
        source = "call-time"
        fallback_model = proj_ov.get("model", base_model) if proj_ov else (
            glob_ov.get("model", base_model) if glob_ov and tier_source != "project-tier" else base_model
        )
        model = call_time_model if call_time_model is not None else fallback_model
        if is_antigravity_bare_alias(clean_platform, model):
            effort = None
        else:
            effort = call_time_effort if call_time_effort is not None else (
                base_effort if base_effort is not None else DEFAULT_EFFORT
            )
    elif proj_ov:
        source = "project-override"
        fallback_model = glob_ov.get("model", base_model) if glob_ov and tier_source != "project-tier" else base_model
        model, effort = _apply_override_layer(clean_platform, proj_ov, fallback_model, base_effort)
    elif tier_source == "project-tier":
        source = "project-tier"
        resolved_tier = base_tier
        model, effort = base_model, base_effort
    elif glob_ov:
        source = "global-override"
        model, effort = _apply_override_layer(clean_platform, glob_ov, base_model, base_effort)
    elif tier_source == "global-tier":
        source = "global-tier"
        resolved_tier = base_tier
        model, effort = base_model, base_effort
    else:
        source = "default"
        resolved_tier = base_tier
        model, effort = base_model, base_effort

    result: dict[str, Any] = {
        "model": model,
        "effort": effort,
        "tier": resolved_tier,
        "source": source,
    }
    if clean_platform == "antigravity":
        alias_tier = resolved_tier
        if alias_tier is None:
            alias_tier = _infer_tier_for_antigravity_model(
                model,
                effective_tiers["antigravity"],
                base_tier,
            )
        result["swarm_model"] = format_antigravity_swarm_model(model, effort)
        result["subagent_alias"] = antigravity_subagent_alias(model, tier=alias_tier)
    return result


def resolve_platform(
    platform: str,
    cwd: str | Path = ".",
    *,
    home: str | Path | None = None,
) -> dict[str, dict[str, Any]]:
    """Resolve all 6 roles for one platform."""
    clean_platform = validate_platform(platform)
    global_cfg = load_config_layer("global", cwd, home=home)
    project_cfg = load_config_layer("project", cwd, home=home)
    return {
        role: resolve_role(
            clean_platform,
            role,
            cwd,
            global_cfg=global_cfg,
            project_cfg=project_cfg,
            home=home,
            _layers_loaded=True,
        )
        for role in ROLES
    }


def resolve_all(
    cwd: str | Path = ".",
    *,
    platforms: tuple[str, ...] | list[str] | None = None,
    home: str | Path | None = None,
) -> dict[str, Any]:
    """Resolve roles across all (or selected) platforms and return full summary mapping."""
    selected = tuple(validate_platform(p) for p in platforms) if platforms else PLATFORMS
    cwd_path = Path(cwd).expanduser().resolve()
    global_path = config_path("global", cwd_path, home=home)
    project_path = config_path("project", cwd_path, home=home)
    global_cfg = load_config_layer("global", cwd_path, home=home)
    project_cfg = load_config_layer("project", cwd_path, home=home)

    effective_preset = DEFAULT_PRESET
    if global_cfg and "preset" in global_cfg:
        effective_preset = global_cfg["preset"]
    if project_cfg and "preset" in project_cfg:
        effective_preset = project_cfg["preset"]

    resolved_platforms = {
        plat: {
            role: resolve_role(
                plat,
                role,
                cwd_path,
                global_cfg=global_cfg,
                project_cfg=project_cfg,
                home=home,
                _layers_loaded=True,
            )
            for role in ROLES
        }
        for plat in selected
    }

    return {
        "schema_version": SCHEMA_VERSION,
        "cwd": str(cwd_path),
        "repo_slug": memory.repo_slug(cwd_path),
        "global_path": str(global_path),
        "project_path": str(project_path),
        "global_exists": global_path.is_file(),
        "project_exists": project_path.is_file(),
        "effective_preset": effective_preset,
        "platforms": resolved_platforms,
    }


def _load_target_for_write(
    scope: str,
    cwd: str | Path,
    *,
    home: str | Path | None = None,
) -> tuple[Path, dict[str, Any]]:
    target = config_path(scope, cwd, home=home)
    existing = load_config_layer(scope, cwd, home=home, strict=True)
    if existing is not None:
        return target, existing
    if scope == "global":
        return target, default_config()
    return target, {"schema_version": SCHEMA_VERSION}


def _save_validated_config(target: Path, data: dict[str, Any]) -> dict[str, Any]:
    validated = validate_config(data)
    serialized = json.dumps(validated, indent=2, ensure_ascii=False) + "\n"
    memory.atomic_write_text(target, serialized)
    return validated


def set_preset(
    preset: str,
    *,
    scope: str = "global",
    cwd: str | Path = ".",
    home: str | Path | None = None,
) -> dict[str, Any]:
    """Apply a built-in preset (`budget`, `balanced`, `quality`) atomically."""
    clean_preset = validate_preset(preset)
    target, cfg = _load_target_for_write(scope, cwd, home=home)
    cfg["preset"] = clean_preset
    cfg["roles"] = _preset_roles_dict(clean_preset)
    _save_validated_config(target, cfg)
    return {"action": "preset", "scope": scope, "preset": clean_preset, "path": str(target)}


def set_provider(
    platform: str,
    provider: str,
    *,
    scope: str = "global",
    cwd: str | Path = ".",
    home: str | Path | None = None,
) -> dict[str, Any]:
    """Switch `tiers.<platform>` atomically to a built-in provider catalog."""
    clean_platform = validate_platform(platform)
    clean_provider = validate_provider(clean_platform, provider)
    target, cfg = _load_target_for_write(scope, cwd, home=home)
    cfg.setdefault("tiers", {})[clean_platform] = _catalog_for(clean_platform, clean_provider)
    _save_validated_config(target, cfg)
    return {
        "action": "set-provider",
        "scope": scope,
        "platform": clean_platform,
        "provider": clean_provider,
        "path": str(target),
    }


def set_role(
    role: str,
    tier: str,
    *,
    effort: str | None = None,
    scope: str = "global",
    cwd: str | Path = ".",
    home: str | Path | None = None,
) -> dict[str, Any]:
    """Set `roles.<role>` to an abstract tier and optional role-level effort."""
    clean_role = validate_role(role)
    clean_tier = validate_tier(tier)
    clean_effort = validate_effort(effort) if effort is not None else None
    target, cfg = _load_target_for_write(scope, cwd, home=home)
    cfg.setdefault("roles", {})[clean_role] = (
        {"tier": clean_tier, "effort": clean_effort}
        if clean_effort is not None
        else clean_tier
    )
    _save_validated_config(target, cfg)
    return {
        "action": "set-role",
        "scope": scope,
        "role": clean_role,
        "tier": clean_tier,
        "effort": clean_effort,
        "path": str(target),
    }


def set_tier(
    platform: str,
    tier: str,
    model: str,
    *,
    effort: str | None = None,
    scope: str = "global",
    cwd: str | Path = ".",
    home: str | Path | None = None,
) -> dict[str, Any]:
    """Customize concrete model/effort for `(platform, tier)`."""
    clean_platform = validate_platform(platform)
    clean_tier = validate_tier(tier)
    clean_model, clean_effort = validate_model_id(clean_platform, model, effort)
    if clean_effort is None and not is_antigravity_bare_alias(clean_platform, clean_model):
        clean_effort = DEFAULT_EFFORT

    target, cfg = _load_target_for_write(scope, cwd, home=home)
    cfg.setdefault("tiers", {}).setdefault(clean_platform, {})[clean_tier] = _serialize_tier_entry(
        clean_model,
        clean_effort,
    )
    _save_validated_config(target, cfg)
    return {
        "action": "set-tier",
        "scope": scope,
        "platform": clean_platform,
        "tier": clean_tier,
        "model": clean_model,
        "effort": clean_effort,
        "path": str(target),
    }


def set_override(
    platform: str,
    role: str,
    *,
    model: str | None = None,
    effort: str | None = None,
    clear: bool = False,
    scope: str = "global",
    cwd: str | Path = ".",
    home: str | Path | None = None,
) -> dict[str, Any]:
    """Pin or clear a per-platform per-role override."""
    clean_platform = validate_platform(platform)
    clean_role = validate_role(role)
    target, cfg = _load_target_for_write(scope, cwd, home=home)

    if clear:
        if model is not None or effort is not None:
            raise ValueError("Cannot combine --clear with --model or --effort")
        overrides = cfg.get("overrides")
        if isinstance(overrides, dict) and isinstance(overrides.get(clean_platform), dict):
            overrides[clean_platform].pop(clean_role, None)
            if scope == "project" and not overrides[clean_platform]:
                overrides.pop(clean_platform, None)
            if scope == "project" and not overrides:
                cfg.pop("overrides", None)
        _save_validated_config(target, cfg)
        return {
            "action": "set-override",
            "scope": scope,
            "platform": clean_platform,
            "role": clean_role,
            "cleared": True,
            "path": str(target),
        }

    if model is None:
        if effort is None:
            raise ValueError("Must specify --model or --clear")
        clean_effort = validate_effort(effort)
        entry: dict[str, str] = {"effort": clean_effort}
        clean_model = None
    else:
        clean_model, clean_effort = validate_model_id(clean_platform, model, effort)
        entry = {"model": clean_model}
        if clean_effort is not None:
            entry["effort"] = clean_effort

    cfg.setdefault("overrides", {}).setdefault(clean_platform, {})[clean_role] = entry
    _save_validated_config(target, cfg)
    return {
        "action": "set-override",
        "scope": scope,
        "platform": clean_platform,
        "role": clean_role,
        "model": clean_model,
        "effort": clean_effort,
        "path": str(target),
    }


def reset_config(
    *,
    scope: str = "global",
    cwd: str | Path = ".",
    home: str | Path | None = None,
) -> dict[str, Any]:
    """Remove `global`, `project`, or `all` custom config files to restore `balanced` defaults."""
    if scope not in ("global", "project", "all"):
        raise ValueError(f"Invalid reset scope {scope!r}; expected 'global', 'project', or 'all'")
    scopes = ("global", "project") if scope == "all" else (scope,)
    removed: list[str] = []
    for item in scopes:
        target = config_path(item, cwd, home=home)
        if target.exists():
            if target.is_dir():
                raise ValueError(f"Config path is a directory, expected file: {target}")
            target.unlink()
            removed.append(str(target))
    return {"action": "reset", "scope": scope, "removed": removed}


def _import_agents():
    """Import `agents.py` from sibling `paddock_core/` or repo `core/config/` when available."""
    here = Path(__file__).resolve().parent
    sibling = here / "agents.py"
    if not sibling.is_file():
        return None
    resolved_sibling = sibling.resolve()
    if (here / "memory.py").is_file():
        for key in ("agents", "paddock_agents_local"):
            existing = sys.modules.get(key)
            existing_file = getattr(existing, "__file__", None)
            if existing is not None and existing_file:
                try:
                    if Path(existing_file).resolve() == resolved_sibling:
                        return existing
                except OSError:
                    pass
        spec = importlib.util.spec_from_file_location("paddock_agents_local", resolved_sibling)
        if spec is None or spec.loader is None:
            raise ImportError(f"Unable to load agents module from {resolved_sibling}")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["paddock_agents_local"] = mod
        spec.loader.exec_module(mod)
        return mod

    repo_root = here.parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from core.config import agents  # type: ignore[import-not-found]

    return agents


def apply_models(
    *,
    platform: str = "all",
    cwd: str | Path = ".",
    home: str | Path | None = None,
    target: str | Path | None = None,
    dry_run: bool = False,
    paddock_home: str | Path | None = None,
    source: str | Path | None = None,
) -> dict[str, Any]:
    """Compile and apply global `~/.paddock/models.json` profiles to installed static `agents/` targets."""
    if platform not in ("antigravity", "claude", "all"):
        raise ValueError(f"Unsupported apply platform {platform!r}; expected 'antigravity', 'claude', or 'all'")
    agents_mod = _import_agents()
    if agents_mod is None or not hasattr(agents_mod, "apply_models_to_targets"):
        raise RuntimeError(
            "Agent compiler (agents.py) is not installed yet; run after canonical agent definitions are synced."
        )
    kwargs: dict[str, Any] = {
        "platform": platform,
        "cwd": cwd,
        "home": home,
        "target": target,
        "dry_run": dry_run,
    }
    if paddock_home is not None:
        kwargs["paddock_home"] = paddock_home
    if source is not None:
        kwargs["source"] = source
    return agents_mod.apply_models_to_targets(**kwargs)


def _format_show_text(summary: dict[str, Any]) -> str:
    lines = [
        f"Paddock Models (schema v{summary['schema_version']}, preset={summary['effective_preset']})",
        f"Repo slug: {summary['repo_slug']}",
        f"Global config:  {summary['global_path']} ({'loaded' if summary['global_exists'] else 'default'})",
        f"Project config: {summary['project_path']} ({'loaded' if summary['project_exists'] else 'none'})",
    ]
    for plat, role_map in summary["platforms"].items():
        lines.append("")
        lines.append(f"[{plat}]")
        if plat == "antigravity":
            lines.append("  role         model               effort   swarm_model                subagent_alias  source")
            for role in ROLES:
                r = role_map[role]
                eff = r["effort"] or "-"
                lines.append(
                    f"  {role:<12} {r['model']:<19} {eff:<8} "
                    f"{r['swarm_model']:<26} {r['subagent_alias']:<15} {r['source']}"
                )
        else:
            lines.append("  role         model                        effort   source")
            for role in ROLES:
                r = role_map[role]
                eff = r["effort"] or "-"
                lines.append(f"  {role:<12} {r['model']:<28} {eff:<8} {r['source']}")
    return "\n".join(lines)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="models.py",
        description="Configure and inspect Paddock multi-platform agent model settings.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_show = sub.add_parser("show", help="Print effective resolved models and source layers")
    p_show.add_argument("--cwd", default=".", help="Workspace path for project config lookup")
    p_show.add_argument("--platform", choices=PLATFORMS, default=None, help="Filter to one platform")
    p_show.add_argument("--json", action="store_true", help="Emit JSON output")

    p_preset = sub.add_parser("preset", help="Apply a built-in role tier/effort preset")
    p_preset.add_argument("preset_name", choices=tuple(PRESETS), metavar="preset")
    p_preset.add_argument("--scope", choices=("global", "project"), default="global")
    p_preset.add_argument("--cwd", default=".")
    p_preset.add_argument("--json", action="store_true")

    p_prov = sub.add_parser("set-provider", help="Switch a platform's tier catalog to a built-in provider")
    p_prov.add_argument("platform", choices=PLATFORMS)
    p_prov.add_argument("provider", help="Provider name (e.g. google, anthropic, openai, native)")
    p_prov.add_argument("--scope", choices=("global", "project"), default="global")
    p_prov.add_argument("--cwd", default=".")
    p_prov.add_argument("--json", action="store_true")

    p_role = sub.add_parser("set-role", help="Set a role's abstract tier and optional effort")
    p_role.add_argument("role", choices=ROLES)
    p_role.add_argument("tier", choices=TIERS)
    p_role.add_argument("--effort", choices=EFFORTS, default=None)
    p_role.add_argument("--scope", choices=("global", "project"), default="global")
    p_role.add_argument("--cwd", default=".")
    p_role.add_argument("--json", action="store_true")

    p_tier = sub.add_parser("set-tier", help="Customize concrete model/effort for a platform tier")
    p_tier.add_argument("platform", choices=PLATFORMS)
    p_tier.add_argument("tier", choices=TIERS)
    p_tier.add_argument("--model", required=True)
    p_tier.add_argument("--effort", choices=EFFORTS, default=None)
    p_tier.add_argument("--scope", choices=("global", "project"), default="global")
    p_tier.add_argument("--cwd", default=".")
    p_tier.add_argument("--json", action="store_true")

    p_ov = sub.add_parser("set-override", help="Pin or clear a per-platform per-role model override")
    p_ov.add_argument("platform", choices=PLATFORMS)
    p_ov.add_argument("role", choices=ROLES)
    ov_group = p_ov.add_mutually_exclusive_group(required=True)
    ov_group.add_argument("--model", default=None)
    ov_group.add_argument("--clear", action="store_true")
    p_ov.add_argument("--effort", choices=EFFORTS, default=None)
    p_ov.add_argument("--scope", choices=("global", "project"), default="global")
    p_ov.add_argument("--cwd", default=".")
    p_ov.add_argument("--json", action="store_true")

    p_reset = sub.add_parser("reset", help="Remove custom config and restore balanced defaults")
    p_reset.add_argument("--scope", choices=("global", "project", "all"), default="global")
    p_reset.add_argument("--cwd", default=".")
    p_reset.add_argument("--json", action="store_true")

    p_apply = sub.add_parser("apply", help="Compile installed static agent profiles from active global config")
    p_apply.add_argument("--platform", choices=("antigravity", "claude", "all"), default="all")
    p_apply.add_argument("--cwd", default=".")
    p_apply.add_argument("--home", default=None)
    p_apply.add_argument("--target", default=None)
    p_apply.add_argument("--dry-run", action="store_true")
    p_apply.add_argument("--json", action="store_true")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    use_json = bool(getattr(args, "json", False))

    try:
        if args.command == "show":
            platforms = (args.platform,) if args.platform else None
            res = resolve_all(args.cwd, platforms=platforms)
            if use_json:
                print(json.dumps(res, indent=2, ensure_ascii=False))
            else:
                print(_format_show_text(res))
            return 0

        if args.command == "preset":
            res = set_preset(args.preset_name, scope=args.scope, cwd=args.cwd)
        elif args.command == "set-provider":
            res = set_provider(args.platform, args.provider, scope=args.scope, cwd=args.cwd)
        elif args.command == "set-role":
            res = set_role(args.role, args.tier, effort=args.effort, scope=args.scope, cwd=args.cwd)
        elif args.command == "set-tier":
            res = set_tier(
                args.platform,
                args.tier,
                args.model,
                effort=args.effort,
                scope=args.scope,
                cwd=args.cwd,
            )
        elif args.command == "set-override":
            res = set_override(
                args.platform,
                args.role,
                model=args.model,
                effort=args.effort,
                clear=args.clear,
                scope=args.scope,
                cwd=args.cwd,
            )
        elif args.command == "reset":
            res = reset_config(scope=args.scope, cwd=args.cwd)
        elif args.command == "apply":
            res = apply_models(
                platform=args.platform,
                cwd=args.cwd,
                home=args.home,
                target=args.target,
                dry_run=args.dry_run,
            )
        else:
            parser.error(f"Unknown command: {args.command}")
            return 2

        if use_json:
            print(json.dumps(res, indent=2, ensure_ascii=False))
        else:
            print(json.dumps(res, ensure_ascii=False))
        return 0
    except (ValueError, OSError, RuntimeError) as exc:
        if use_json:
            print(json.dumps({"error": str(exc)}), file=sys.stderr)
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
