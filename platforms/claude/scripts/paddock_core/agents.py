# /// script
# requires-python = ">=3.12"
# ///
"""Canonical Paddock agent parser, validator, platform compiler, and profile applier."""

from __future__ import annotations

from dataclasses import dataclass
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any


def _load_sibling_or_repo_module(mod_name: str, sibling_file: Path, repo_pkg: str, repo_attr: str):
    """Load `memory` or `models` cleanly from sibling `paddock_core/` or repo `core/`."""
    resolved_sibling = sibling_file.resolve()
    if sibling_file.is_file():
        main_mod = sys.modules.get("__main__")
        main_file = getattr(main_mod, "__file__", None)
        if main_mod is not None and main_file:
            try:
                if Path(main_file).resolve() == resolved_sibling:
                    return main_mod
            except OSError:
                pass
        for key in (mod_name, f"paddock_{mod_name}_local"):
            existing = sys.modules.get(key)
            existing_file = getattr(existing, "__file__", None)
            if existing is not None and existing_file:
                try:
                    if Path(existing_file).resolve() == resolved_sibling:
                        return existing
                except OSError:
                    pass
        spec = importlib.util.spec_from_file_location(f"paddock_{mod_name}_local", resolved_sibling)
        if spec is None or spec.loader is None:
            raise ImportError(f"Unable to load {mod_name} from {resolved_sibling}")
        loaded = importlib.util.module_from_spec(spec)
        sys.modules[f"paddock_{mod_name}_local"] = loaded
        spec.loader.exec_module(loaded)
        return loaded

    here = Path(__file__).resolve().parent
    repo_root = here.parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    pkg = __import__(repo_pkg, fromlist=[repo_attr])
    return getattr(pkg, repo_attr)


def _import_memory():
    here = Path(__file__).resolve().parent
    sibling = here / "memory.py"
    if sibling.is_file():
        return _load_sibling_or_repo_module("memory", sibling, "core.memory", "memory")
    repo_mem = here.parent / "memory" / "memory.py"
    if repo_mem.is_file():
        return _load_sibling_or_repo_module("memory", Path("__missing__"), "core.memory", "memory")
    raise ImportError("Unable to locate Paddock memory.py")


def _import_models():
    here = Path(__file__).resolve().parent
    sibling = here / "models.py"
    if sibling.is_file():
        return _load_sibling_or_repo_module("models", sibling, "core.config", "models")
    raise ImportError("Unable to locate Paddock models.py")


memory = _import_memory()
models = _import_models()

CANONICAL_ROLES: tuple[str, ...] = models.ROLES
VALID_TIERS: tuple[str, ...] = models.TIERS
VALID_PLATFORMS: tuple[str, ...] = models.PLATFORMS
SUPPORTED_RENDER_PLATFORMS: tuple[str, ...] = ("antigravity", "claude")
VALID_CAPABILITIES: tuple[str, ...] = (
    "read",
    "edit",
    "write",
    "shell",
    "tasks",
    "search",
    "web",
    "delegate",
    "mcp",
)
REQUIRED_FRONTMATTER_KEYS: tuple[str, ...] = (
    "name",
    "description",
    "default_tier",
    "platforms",
    "capabilities",
    "disallowed",
)
ALLOWED_FRONTMATTER_KEYS: frozenset[str] = frozenset(REQUIRED_FRONTMATTER_KEYS)

CAPABILITY_TOOL_MAP: dict[str, dict[str, tuple[str, ...]]] = {
    "antigravity": {
        "read": ("read_file",),
        "edit": ("edit_file",),
        "write": ("write_file",),
        "shell": ("run_command",),
        "tasks": ("manage_task",),
        "search": (),
        "web": ("search_web",),
        "delegate": (),
        "mcp": ("call_mcp_tool",),
    },
    "claude": {
        "read": ("Read",),
        "edit": ("Edit",),
        "write": ("Write",),
        "shell": ("Bash",),
        "tasks": (),
        "search": ("Grep", "Glob"),
        "web": ("WebFetch", "WebSearch"),
        "delegate": ("Agent",),
        "mcp": (
            "mcp__plugin_paddock_paddock-worktree__worktree_create",
            "mcp__plugin_paddock_paddock-worktree__worktree_list",
            "mcp__plugin_paddock_paddock-worktree__worktree_remove",
        ),
    },
}

ALLOWED_ANTIGRAVITY_FILENAMES: frozenset[str] = frozenset(
    f"paddock-{role}.md" for role in CANONICAL_ROLES if role != "builder"
)
ALLOWED_CLAUDE_FILENAMES: frozenset[str] = frozenset(f"{role}.md" for role in CANONICAL_ROLES)

_UNSAFE_YAML_PREFIXES = ("!", "&", "*", "%", "@", "`", "{", "}", "|", ">")
_KEY_RE = re.compile(r"\A[a-z][a-z0-9_]*\Z")


@dataclass(frozen=True)
class CanonicalAgent:
    """Validated canonical Paddock agent definition."""

    name: str
    description: str
    default_tier: str
    platforms: tuple[str, ...]
    capabilities: tuple[str, ...]
    disallowed: tuple[str, ...]
    body: str
    source_path: Path | None = None


def _is_symlink_or_junction(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(callable(is_junction) and is_junction())


def _has_unsafe_control_chars(text: str, *, allow_newlines: bool, allow_tabs: bool) -> bool:
    for ch in text:
        code = ord(ch)
        if ch == "\n":
            if not allow_newlines:
                return True
            continue
        if ch == "\t":
            if not allow_tabs:
                return True
            continue
        if code < 0x20 or 0x7F <= code <= 0x9F or code in (0x2028, 0x2029):
            return True
    return False


def _strip_inline_comment(raw: str) -> str:
    in_single = False
    in_double = False
    escaped = False
    for idx, ch in enumerate(raw):
        if escaped:
            escaped = False
            continue
        if ch == "\\" and in_double:
            escaped = True
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            if idx == 0 or raw[idx - 1].isspace():
                return raw[:idx].rstrip()
    return raw.rstrip()


def _parse_yaml_scalar(raw: str) -> str:
    val = _strip_inline_comment(raw).strip()
    if not val:
        return ""
    if (val.startswith('"') and val.endswith('"')) or (
        val.startswith("'") and val.endswith("'")
    ):
        if len(val) < 2:
            raise ValueError(f"Unterminated quote in YAML scalar: {raw!r}")
        inner = val[1:-1]
        if val[0] == '"':
            if "\\" in inner:
                # Reject control escape sequences such as \n, \r, \x00, \u0000
                out: list[str] = []
                i = 0
                while i < len(inner):
                    ch = inner[i]
                    if ch == "\\":
                        if i + 1 >= len(inner):
                            raise ValueError(f"Dangling backslash in YAML scalar: {raw!r}")
                        nxt = inner[i + 1]
                        if nxt in ('"', "\\", "/"):
                            out.append(nxt)
                            i += 2
                        else:
                            raise ValueError(f"Unsupported YAML escape \\{nxt} in scalar: {raw!r}")
                    else:
                        out.append(ch)
                        i += 1
                inner = "".join(out)
        else:
            inner = inner.replace("''", "'")
        if _has_unsafe_control_chars(inner, allow_newlines=False, allow_tabs=False):
            raise ValueError(f"Control character rejected in YAML scalar: {raw!r}")
        return inner

    if '"' in val or "'" in val:
        raise ValueError(f"Unbalanced quote in YAML scalar: {raw!r}")
    if val.startswith(_UNSAFE_YAML_PREFIXES) or "!!" in val:
        raise ValueError(f"Unsafe YAML token rejected in scalar: {raw!r}")
    if _has_unsafe_control_chars(val, allow_newlines=False, allow_tabs=False):
        raise ValueError(f"Control character rejected in YAML scalar: {raw!r}")
    return val


def _parse_inline_yaml_list(raw: str) -> list[str]:
    stripped = _strip_inline_comment(raw).strip()
    if not (stripped.startswith("[") and stripped.endswith("]")):
        raise ValueError(f"Malformed inline YAML list: {raw!r}")
    inner = stripped[1:-1].strip()
    if not inner:
        return []
    items: list[str] = []
    cur: list[str] = []
    in_single = False
    in_double = False
    escaped = False
    for ch in inner:
        if escaped:
            cur.append(ch)
            escaped = False
            continue
        if ch == "\\" and in_double:
            cur.append(ch)
            escaped = True
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
            cur.append(ch)
        elif ch == '"' and not in_single:
            in_double = not in_double
            cur.append(ch)
        elif ch == "," and not in_single and not in_double:
            token = _parse_yaml_scalar("".join(cur))
            if not token:
                raise ValueError(f"Empty item in YAML list: {raw!r}")
            items.append(token)
            cur = []
        else:
            cur.append(ch)
    if in_single or in_double:
        raise ValueError(f"Unterminated quote in YAML list: {raw!r}")
    last = _parse_yaml_scalar("".join(cur))
    if not last:
        raise ValueError(f"Trailing comma or empty item in YAML list: {raw!r}")
    items.append(last)
    return items


def _format_yaml_scalar(value: str) -> str:
    if _has_unsafe_control_chars(value, allow_newlines=False, allow_tabs=False):
        raise ValueError(f"Control character rejected when serializing YAML scalar: {value!r}")
    needs_quote = (
        value == ""
        or value in ("true", "false", "null", "~")
        or value[0].isspace()
        or value[-1].isspace()
        or value.startswith(_UNSAFE_YAML_PREFIXES)
        or any(ch in value for ch in (":", "#", "[", "]", "{", "}", ",", '"', "'"))
    )
    if not needs_quote:
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _validate_unique_tokens(
    raw_items: Any,
    *,
    field: str,
    allowed: tuple[str, ...],
    allow_empty: bool,
) -> tuple[str, ...]:
    if not isinstance(raw_items, list):
        raise ValueError(f"Canonical field {field!r} must be a list")
    if not allow_empty and not raw_items:
        raise ValueError(f"Canonical field {field!r} must not be empty")
    seen: set[str] = set()
    result: list[str] = []
    for item in raw_items:
        if not isinstance(item, str) or item not in allowed:
            raise ValueError(
                f"Invalid entry {item!r} in canonical field {field!r}; expected one of {allowed}"
            )
        if item in seen:
            raise ValueError(f"Duplicate entry {item!r} in canonical field {field!r}")
        seen.add(item)
        result.append(item)
    return tuple(result)


def parse_canonical_agent_text(
    text: str,
    *,
    file_stem: str | None = None,
    source_path: Path | None = None,
) -> CanonicalAgent:
    """Parse and strictly validate a canonical `core/agents/<role>.md` definition."""
    if not isinstance(text, str):
        raise ValueError("Agent definition text must be a string")
    normalized = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    if not lines or lines[0].strip() != "---":
        raise ValueError("Missing opening YAML frontmatter delimiter '---'")

    end_idx: int | None = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            end_idx = idx
            break
    if end_idx is None:
        raise ValueError("Missing closing YAML frontmatter delimiter '---'")

    fm_lines = lines[1:end_idx]
    body = "\n".join(lines[end_idx + 1 :]).lstrip("\n").rstrip() + "\n"
    if _has_unsafe_control_chars(body, allow_newlines=True, allow_tabs=True):
        raise ValueError("Unsafe control character rejected in agent body")
    if not body.strip():
        raise ValueError("Canonical agent body must not be empty")

    parsed: dict[str, Any] = {}
    current_list_key: str | None = None

    for raw_line in fm_lines:
        if _has_unsafe_control_chars(raw_line, allow_newlines=False, allow_tabs=False):
            raise ValueError(f"Unsafe control or tab character in frontmatter line: {raw_line!r}")
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped == "..." or stripped.startswith("%"):
            raise ValueError(f"Unsupported YAML directive/marker in frontmatter: {raw_line!r}")
        if "!!" in stripped:
            raise ValueError(f"Unsafe YAML tag rejected in frontmatter: {raw_line!r}")

        indent = len(raw_line) - len(raw_line.lstrip(" "))
        if indent > 0:
            if current_list_key is None or not stripped.startswith("- "):
                raise ValueError(f"Unexpected nested YAML indentation in frontmatter: {raw_line!r}")
            item_val = _parse_yaml_scalar(stripped[2:])
            if not item_val:
                raise ValueError(f"Empty YAML list item in frontmatter: {raw_line!r}")
            parsed[current_list_key].append(item_val)
            continue

        current_list_key = None
        if ":" not in stripped:
            raise ValueError(f"Malformed frontmatter line: {raw_line!r}")
        key, rest = stripped.split(":", 1)
        key = key.strip()
        if not _KEY_RE.fullmatch(key):
            raise ValueError(f"Invalid frontmatter key {key!r}")
        if key not in ALLOWED_FRONTMATTER_KEYS:
            raise ValueError(f"Unknown canonical frontmatter key {key!r}")
        if key in parsed:
            raise ValueError(f"Duplicate canonical frontmatter key {key!r}")

        rest_clean = _strip_inline_comment(rest).strip()
        if key in ("platforms", "capabilities", "disallowed"):
            if not rest_clean:
                parsed[key] = []
                current_list_key = key
            elif rest_clean.startswith("[") and rest_clean.endswith("]"):
                parsed[key] = _parse_inline_yaml_list(rest_clean)
            else:
                raise ValueError(f"Canonical list field {key!r} must be a YAML list: {raw_line!r}")
        else:
            if not rest_clean:
                raise ValueError(f"Canonical scalar field {key!r} must not be empty")
            if rest_clean.startswith("[") or rest_clean.startswith("{"):
                raise ValueError(f"Canonical scalar field {key!r} cannot be a collection")
            parsed[key] = _parse_yaml_scalar(rest)

    missing = [k for k in REQUIRED_FRONTMATTER_KEYS if k not in parsed]
    if missing:
        raise ValueError(f"Missing required canonical frontmatter keys: {missing}")

    name = parsed["name"]
    if not isinstance(name, str) or name not in CANONICAL_ROLES:
        raise ValueError(f"Invalid canonical agent name {name!r}; expected one of {CANONICAL_ROLES}")
    if file_stem is not None and name != file_stem:
        raise ValueError(f"Canonical agent name {name!r} does not match filename stem {file_stem!r}")

    description = parsed["description"]
    if not isinstance(description, str) or not description.strip():
        raise ValueError("Canonical agent description must be a non-empty single-line string")
    if _has_unsafe_control_chars(description, allow_newlines=False, allow_tabs=False):
        raise ValueError("Control character rejected in canonical agent description")

    default_tier = parsed["default_tier"]
    if not isinstance(default_tier, str) or default_tier not in VALID_TIERS:
        raise ValueError(
            f"Invalid default_tier {default_tier!r}; expected one of {VALID_TIERS}"
        )

    platforms = _validate_unique_tokens(
        parsed["platforms"], field="platforms", allowed=VALID_PLATFORMS, allow_empty=False
    )
    capabilities = _validate_unique_tokens(
        parsed["capabilities"], field="capabilities", allowed=VALID_CAPABILITIES, allow_empty=False
    )
    disallowed = _validate_unique_tokens(
        parsed["disallowed"], field="disallowed", allowed=VALID_CAPABILITIES, allow_empty=True
    )
    overlap = set(capabilities) & set(disallowed)
    if overlap:
        raise ValueError(
            f"Capabilities cannot appear in both 'capabilities' and 'disallowed': {sorted(overlap)}"
        )

    return CanonicalAgent(
        name=name,
        description=description,
        default_tier=default_tier,
        platforms=platforms,
        capabilities=capabilities,
        disallowed=disallowed,
        body=body,
        source_path=source_path,
    )


def _validate_raw_path_no_traversal(raw: str | Path, *, label: str) -> Path:
    raw_str = str(raw)
    if not raw_str or not raw_str.strip():
        raise ValueError(f"Empty path rejected for {label}")
    if "\x00" in raw_str or "\n" in raw_str or "\r" in raw_str:
        raise ValueError(f"Control character rejected in {label} path: {raw_str!r}")
    segments = [seg for seg in re.split(r"[/\\]", raw_str) if seg]
    if ".." in segments or ".." in Path(raw_str).parts:
        raise ValueError(f"Path traversal '..' rejected in {label} path: {raw_str!r}")
    return Path(raw_str).expanduser()


def resolve_canonical_agents_dir(source: str | Path | None = None) -> Path:
    """Locate canonical agents dir (`paddock_core/agents/` first, falling back to `core/agents/`)."""
    if source is not None:
        raw = _validate_raw_path_no_traversal(source, label="canonical source")
        if _is_symlink_or_junction(raw):
            raise ValueError(f"Refusing symlinked canonical source directory: {raw}")
        candidates = (raw / "core" / "agents", raw / "agents", raw)
        for cand in candidates:
            if _is_symlink_or_junction(cand):
                raise ValueError(f"Refusing symlinked canonical agents directory: {cand}")
            if cand.is_dir() and any(cand.glob("*.md")):
                return cand.resolve()
        raise FileNotFoundError(f"No canonical agent markdown files found under {raw}")

    here = Path(__file__).resolve().parent
    candidates: list[Path] = [
        here / "agents",        # Installed / synced standalone `paddock_core/agents/`
        here.parent / "agents", # Repo `core/agents/` when running from `core/config/agents.py`
    ]
    if len(here.parents) >= 3:
        candidates.append(here.parents[2] / "core" / "agents")

    for cand in candidates:
        if _is_symlink_or_junction(cand):
            raise ValueError(f"Refusing symlinked canonical agents directory: {cand}")
        if cand.is_dir() and any(cand.glob("*.md")):
            return cand.resolve()

    raise FileNotFoundError(
        "Unable to locate canonical agents directory (checked paddock_core/agents/ and core/agents/)"
    )


def load_canonical_agent(path: Path) -> CanonicalAgent:
    """Load and validate one canonical `<role>.md` file."""
    if _is_symlink_or_junction(path):
        raise ValueError(f"Refusing symlinked canonical agent file: {path}")
    text = path.read_text(encoding="utf-8")
    return parse_canonical_agent_text(text, file_stem=path.stem, source_path=path)


def load_canonical_agents(source: str | Path | None = None) -> dict[str, CanonicalAgent]:
    """Load and validate all canonical agent definitions from `source`."""
    agents_dir = resolve_canonical_agents_dir(source)
    loaded: dict[str, CanonicalAgent] = {}
    for md_path in sorted(agents_dir.iterdir()):
        if md_path.suffix != ".md":
            continue
        if _is_symlink_or_junction(md_path) or not md_path.is_file():
            raise ValueError(f"Invalid canonical agent entry (non-file or symlink): {md_path}")
        agent = load_canonical_agent(md_path)
        if agent.name in loaded:
            raise ValueError(f"Duplicate canonical role {agent.name!r} in {agents_dir}")
        loaded[agent.name] = agent

    if not loaded:
        raise FileNotFoundError(f"No canonical agent definitions found in {agents_dir}")
    return loaded


def map_capabilities(capabilities: tuple[str, ...] | list[str], platform: str) -> list[str]:
    """Map canonical capabilities to platform-native tool names preserving order."""
    if platform not in CAPABILITY_TOOL_MAP:
        raise ValueError(
            f"Unsupported compilation platform {platform!r}; expected one of {SUPPORTED_RENDER_PLATFORMS}"
        )
    table = CAPABILITY_TOOL_MAP[platform]
    mapped: list[str] = []
    for cap in capabilities:
        if cap not in table:
            raise ValueError(f"Unknown capability {cap!r}")
        mapped.extend(table[cap])
    return list(dict.fromkeys(mapped))


def _antigravity_footer(role: str) -> str:
    if role == "verifier":
        return (
            "Output:\n"
            "- Inside `run_workflow`, return `VERDICT_SCHEMA` via `finish` "
            "(`verdict`, `summary`, `evidence`, `unverified`, optional `reason`).\n"
            "- Standalone via `invoke_subagent`, include `## Evidence`, `## Unverified`, "
            "and a single final verdict line (`PASS`, `FAIL: impl`, `FAIL: plan`, "
            "`FAIL: spec`, or `FAIL: env`)."
        )
    return (
        "Output:\n"
        "- Inside `run_workflow`, submit structured JSON via `finish` "
        "(`status`, `summary`, `evidence`, `unverified`, optional `dispute_evidence`).\n"
        "- Standalone via `invoke_subagent`, end your reply with `## Evidence` and `## Unverified`."
    )


def _claude_footer(role: str) -> str:
    if role == "verifier":
        return (
            "Output:\n"
            "- End every reply with `## Evidence`, `## Unverified`, and **exactly one** final verdict line:\n"
            "  `PASS` | `FAIL: impl` | `FAIL: plan` | `FAIL: spec` | `FAIL: env`"
        )
    return (
        "Output:\n"
        "- End every reply with `## Evidence` and `## Unverified`."
    )


def render_antigravity_agent(agent: CanonicalAgent) -> str:
    """Compile a canonical agent into Antigravity `paddock-<role>.md` format."""
    if "antigravity" not in agent.platforms:
        raise ValueError(f"Role {agent.name!r} does not target 'antigravity'")
    native_name = f"paddock-{agent.name}"
    tools = map_capabilities(agent.capabilities, "antigravity")

    lines = [
        "---",
        f"name: {_format_yaml_scalar(native_name)}",
        f"description: {_format_yaml_scalar(agent.description)}",
        "subagent: true",
        "mainAgent: false",
    ]
    if tools:
        lines.append("tools:")
        for tool in tools:
            lines.append(f"  - {_format_yaml_scalar(tool)}")
    else:
        lines.append("tools: []")
    lines.append("---")

    body = agent.body.replace("\r\n", "\n").replace("\r", "\n").rstrip()
    return "\n".join(lines) + "\n" + body + "\n\n" + _antigravity_footer(agent.name) + "\n"


def render_claude_agent(
    agent: CanonicalAgent,
    *,
    model: str,
    effort: str | None = None,
) -> str:
    """Compile a canonical agent into Claude Code `<role>.md` format."""
    if "claude" not in agent.platforms:
        raise ValueError(f"Role {agent.name!r} does not target 'claude'")
    clean_model, clean_effort = models.validate_model_id("claude", model, effort or models.DEFAULT_EFFORT)
    resolved_effort = clean_effort or models.DEFAULT_EFFORT

    tools = map_capabilities(agent.capabilities, "claude")
    disallowed_tools = map_capabilities(agent.disallowed, "claude")

    lines = [
        "---",
        f"name: {_format_yaml_scalar(agent.name)}",
        f"description: {_format_yaml_scalar(agent.description)}",
        f"model: {_format_yaml_scalar(clean_model)}",
        f"effort: {_format_yaml_scalar(resolved_effort)}",
        f"tools: {', '.join(tools)}",
    ]
    if disallowed_tools:
        lines.append(f"disallowedTools: {', '.join(disallowed_tools)}")
    lines.append("---")

    body = agent.body.replace("\r\n", "\n").replace("\r", "\n").rstrip()
    return "\n".join(lines) + "\n" + body + "\n\n" + _claude_footer(agent.name) + "\n"


def _render_loaded_platform(
    canonical: dict[str, CanonicalAgent],
    platform: str,
    validated_cfg: dict[str, Any],
) -> dict[str, str]:
    if platform not in SUPPORTED_RENDER_PLATFORMS:
        raise ValueError(
            f"Unsupported compilation platform {platform!r}; expected one of {SUPPORTED_RENDER_PLATFORMS}"
        )

    rendered: dict[str, str] = {}
    for role in CANONICAL_ROLES:
        agent = canonical.get(role)
        if agent is None or platform not in agent.platforms:
            continue
        if platform == "antigravity":
            filename = f"paddock-{role}.md"
            if filename not in ALLOWED_ANTIGRAVITY_FILENAMES:
                raise ValueError(f"Unexpected Antigravity agent filename: {filename!r}")
            rendered[filename] = render_antigravity_agent(agent)
        else:
            filename = f"{role}.md"
            if filename not in ALLOWED_CLAUDE_FILENAMES:
                raise ValueError(f"Unexpected Claude agent filename: {filename!r}")
            resolved = models.resolve_role(
                "claude",
                role,
                global_cfg=validated_cfg,
                project_cfg={},
                _layers_loaded=True,
            )
            rendered[filename] = render_claude_agent(
                agent,
                model=resolved["model"],
                effort=resolved.get("effort"),
            )
    return rendered


def render_platform(
    source: str | Path | None,
    platform: str,
    config: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Compile all canonical agents targeting `platform` into `{filename: markdown}`."""
    if platform not in SUPPORTED_RENDER_PLATFORMS:
        raise ValueError(
            f"Unsupported compilation platform {platform!r}; expected one of {SUPPORTED_RENDER_PLATFORMS}"
        )
    canonical = load_canonical_agents(source)
    validated_cfg = models.validate_config(config) if config is not None else models.default_config()
    return _render_loaded_platform(canonical, platform, validated_cfg)


def render_all_platforms(
    source: str | Path | None = None,
    config: dict[str, Any] | None = None,
    *,
    platforms: tuple[str, ...] | list[str] = SUPPORTED_RENDER_PLATFORMS,
) -> dict[str, dict[str, str]]:
    """Compile canonical agents across selected platforms into `{platform: {filename: markdown}}`."""
    selected = tuple(platforms)
    for plat in selected:
        if plat not in SUPPORTED_RENDER_PLATFORMS:
            raise ValueError(
                f"Unsupported compilation platform {plat!r}; expected one of {SUPPORTED_RENDER_PLATFORMS}"
            )
    canonical = load_canonical_agents(source)
    validated_cfg = models.validate_config(config) if config is not None else models.default_config()
    return {
        plat: _render_loaded_platform(canonical, plat, validated_cfg)
        for plat in selected
    }


def _check_non_symlink_descendants(base: Path, target: Path) -> Path:
    """Verify `base` and every relative segment down to `target` are non-symlink and contained."""
    if _is_symlink_or_junction(base):
        raise ValueError(f"Refusing symlinked directory: {base}")
    resolved_base = base.resolve()
    try:
        rel = target.relative_to(base)
    except ValueError as exc:
        raise ValueError(f"Target path {target} escapes base directory {base}") from exc

    cur = base
    for part in rel.parts:
        cur = cur / part
        if _is_symlink_or_junction(cur):
            raise ValueError(f"Refusing symlinked path component: {cur}")

    resolved_target = target.resolve()
    if not resolved_target.is_relative_to(resolved_base):
        raise ValueError(f"Resolved target {resolved_target} escapes base {resolved_base}")
    return resolved_target


def _assert_not_tracked_repo_target(agents_dir: Path) -> None:
    """Refuse writing into tracked Paddock repository directories."""
    resolved = agents_dir.resolve()
    for anc in (resolved, *resolved.parents):
        if (anc / "core" / "agents").is_dir() and (anc / "platforms").is_dir():
            if resolved.is_relative_to(anc / "platforms") or resolved.is_relative_to(anc / "core"):
                raise ValueError(
                    f"Refusing to modify tracked Paddock repository directory: {agents_dir}"
                )

    git_root: Path | None = None
    existing_probe = resolved
    while not existing_probe.exists() and existing_probe != existing_probe.parent:
        existing_probe = existing_probe.parent
    for anc in (existing_probe, *existing_probe.parents):
        if (anc / ".git").exists():
            git_root = anc
            break

    if git_root is not None and resolved.exists():
        try:
            rel_dir = resolved.relative_to(git_root).as_posix()
            proc = subprocess.run(
                ["git", "-C", str(git_root), "ls-files", "--", rel_dir],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                raise ValueError(
                    f"Refusing to modify git-tracked files under {agents_dir}"
                )
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            if isinstance(exc, ValueError):
                raise


def _validate_explicit_target_dir(
    raw_target: str | Path,
    *,
    user_home: Path | None = None,
) -> Path:
    target_path = _validate_raw_path_no_traversal(raw_target, label="target")
    if _is_symlink_or_junction(target_path):
        raise ValueError(f"Refusing symlinked target directory: {target_path}")
    if target_path.exists() and not target_path.is_dir():
        raise ValueError(f"Expected target directory, found non-directory: {target_path}")
    resolved_root = target_path.resolve()
    if target_path.name == "agents":
        agents_dir = target_path
    else:
        agents_dir = target_path / "agents"
        if _is_symlink_or_junction(agents_dir):
            raise ValueError(f"Refusing symlinked agents directory: {agents_dir}")
    if agents_dir.exists() and not agents_dir.is_dir():
        raise ValueError(f"Expected agents directory, found non-directory: {agents_dir}")
    resolved_agents = agents_dir.resolve()
    if not resolved_agents.is_relative_to(resolved_root):
        raise ValueError(f"Resolved agents directory escapes target root: {resolved_agents}")
    if user_home is not None:
        resolved_user_home = user_home.resolve()
        if resolved_agents.is_relative_to(resolved_user_home):
            rel = resolved_agents.relative_to(resolved_user_home)
            cur = user_home
            for part in rel.parts:
                cur = cur / part
                if _is_symlink_or_junction(cur):
                    raise ValueError(f"Refusing symlinked path component: {cur}")
    _assert_not_tracked_repo_target(resolved_agents)
    return resolved_agents


def _discover_antigravity_targets(user_home: Path) -> list[Path]:
    plugins_root = user_home / ".gemini" / "config" / "plugins"
    paddock_plugin = plugins_root / "paddock"
    agents_dir = paddock_plugin / "agents"
    _check_non_symlink_descendants(user_home, agents_dir)
    if not paddock_plugin.exists():
        return []
    if not paddock_plugin.is_dir():
        raise ValueError(f"Expected Antigravity plugin directory, found file: {paddock_plugin}")
    if agents_dir.exists() and not agents_dir.is_dir():
        raise ValueError(f"Expected Antigravity agents directory, found file: {agents_dir}")
    _assert_not_tracked_repo_target(agents_dir)
    return [agents_dir.resolve()]


def _is_claude_paddock_plugin_dir(plugin_dir: Path) -> bool:
    manifest = plugin_dir / ".claude-plugin" / "plugin.json"
    if _is_symlink_or_junction(plugin_dir / ".claude-plugin") or _is_symlink_or_junction(manifest):
        raise ValueError(f"Refusing symlinked Claude plugin metadata under {plugin_dir}")
    if manifest.is_file():
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("name") == "paddock":
                return True
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            pass
    agents_sub = plugin_dir / "agents"
    if _is_symlink_or_junction(agents_sub):
        raise ValueError(f"Refusing symlinked Claude agents directory: {agents_sub}")
    return agents_sub.is_dir() and (plugin_dir.name == "paddock" or plugin_dir.parent.name == "paddock")


def _discover_claude_targets(user_home: Path) -> list[Path]:
    plugins_root = user_home / ".claude" / "plugins"
    _check_non_symlink_descendants(user_home, plugins_root)
    if not plugins_root.exists():
        return []
    if not plugins_root.is_dir():
        raise ValueError(f"Expected Claude plugins directory, found file: {plugins_root}")

    resolved_plugins_root = plugins_root.resolve()
    discovered: list[Path] = []
    seen: set[Path] = set()

    def _add_plugin_dir(install_dir: Path) -> None:
        agents_dir = install_dir / "agents"
        resolved_agents = _check_non_symlink_descendants(plugins_root, agents_dir)
        if agents_dir.exists() and not agents_dir.is_dir():
            raise ValueError(f"Expected Claude agents directory, found file: {agents_dir}")
        _assert_not_tracked_repo_target(resolved_agents)
        if resolved_agents not in seen:
            seen.add(resolved_agents)
            discovered.append(resolved_agents)

    installed_json = plugins_root / "installed_plugins.json"
    if _is_symlink_or_junction(installed_json):
        raise ValueError(f"Refusing symlinked Claude installed_plugins.json: {installed_json}")
    if installed_json.is_file():
        try:
            raw_data = json.loads(installed_json.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid Claude installed_plugins.json: {exc}") from exc
        plugins_map = raw_data.get("plugins", raw_data) if isinstance(raw_data, dict) else {}
        if isinstance(plugins_map, dict):
            for key, entry in plugins_map.items():
                if key != "paddock" and not str(key).startswith("paddock@"):
                    continue
                items = entry if isinstance(entry, list) else ([entry] if isinstance(entry, dict) else [])
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    raw_install = item.get("installPath")
                    if not isinstance(raw_install, str) or not raw_install.strip():
                        continue
                    install_path = _validate_raw_path_no_traversal(
                        raw_install, label="Claude installPath"
                    )
                    if _is_symlink_or_junction(install_path):
                        raise ValueError(f"Refusing symlinked Claude installPath: {install_path}")
                    if not install_path.is_relative_to(plugins_root):
                        # Also check resolved root containment in case of OS normalized drive casing
                        resolved_inst = install_path.resolve()
                        if not resolved_inst.is_relative_to(resolved_plugins_root):
                            raise ValueError(
                                f"Claude installPath escapes {plugins_root}: {raw_install!r}"
                            )
                        try:
                            rel_from_root = resolved_inst.relative_to(resolved_plugins_root)
                            install_path = plugins_root / rel_from_root
                        except ValueError as exc:
                            raise ValueError(
                                f"Claude installPath escapes {plugins_root}: {raw_install!r}"
                            ) from exc
                    _check_non_symlink_descendants(plugins_root, install_path)
                    if install_path.is_dir():
                        _add_plugin_dir(install_path)

    cache_dir = plugins_root / "cache"
    if _is_symlink_or_junction(cache_dir):
        raise ValueError(f"Refusing symlinked Claude cache directory: {cache_dir}")
    if cache_dir.is_dir():
        queue: list[tuple[Path, int]] = [(cache_dir, 0)]
        while queue:
            cur_dir, depth = queue.pop(0)
            if _is_symlink_or_junction(cur_dir):
                raise ValueError(f"Refusing symlinked directory in Claude cache: {cur_dir}")
            if cur_dir != cache_dir and _is_claude_paddock_plugin_dir(cur_dir):
                _add_plugin_dir(cur_dir)
                continue
            if depth >= 4:
                continue
            for child in sorted(cur_dir.iterdir()):
                if _is_symlink_or_junction(child):
                    raise ValueError(f"Refusing symlinked entry in Claude cache: {child}")
                if child.is_dir():
                    queue.append((child, depth + 1))

    return discovered


def _resolve_apply_config_home(
    *,
    user_home: Path | None,
    paddock_home: str | Path | None,
) -> Path | None:
    if paddock_home is not None:
        raw_ph = _validate_raw_path_no_traversal(paddock_home, label="paddock_home")
        if _is_symlink_or_junction(raw_ph):
            raise ValueError(f"Refusing symlinked paddock_home directory: {raw_ph}")
        return raw_ph.resolve()
    if os.environ.get("PADDOCK_HOME", "").strip():
        return None
    if user_home is not None:
        raw_ph = user_home / ".paddock"
        if _is_symlink_or_junction(raw_ph):
            raise ValueError(f"Refusing symlinked Paddock home directory: {raw_ph}")
        return raw_ph.resolve()
    return None


def _allowed_filenames_for(platform: str) -> frozenset[str]:
    if platform == "antigravity":
        return ALLOWED_ANTIGRAVITY_FILENAMES
    if platform == "claude":
        return ALLOWED_CLAUDE_FILENAMES
    raise ValueError(f"Unsupported platform {platform!r}")


def apply_models_to_targets(
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
        raise ValueError(
            f"Unsupported apply platform {platform!r}; expected 'antigravity', 'claude', or 'all'"
        )
    if target is not None and platform == "all":
        raise ValueError("--target requires an explicit single --platform ('antigravity' or 'claude')")

    user_home: Path | None = None
    if home is not None:
        raw_home = _validate_raw_path_no_traversal(home, label="home")
        if _is_symlink_or_junction(raw_home):
            raise ValueError(f"Refusing symlinked home directory: {raw_home}")
        user_home = raw_home
    elif target is None:
        default_home = Path.home()
        if _is_symlink_or_junction(default_home):
            raise ValueError(f"Refusing symlinked home directory: {default_home}")
        user_home = default_home

    cfg_home = _resolve_apply_config_home(user_home=user_home, paddock_home=paddock_home)
    global_cfg_path = models.config_path("global", cwd, home=cfg_home)
    global_cfg = models.load_config_layer("global", cwd, home=cfg_home)
    effective_global_cfg = global_cfg if global_cfg is not None else models.default_config()

    selected_platforms = SUPPORTED_RENDER_PLATFORMS if platform == "all" else (platform,)
    canonical_dir = resolve_canonical_agents_dir(source)
    rendered_by_platform = render_all_platforms(
        canonical_dir, effective_global_cfg, platforms=selected_platforms
    )

    target_pairs: list[tuple[str, Path]] = []
    if target is not None:
        target_pairs.append((platform, _validate_explicit_target_dir(target, user_home=user_home)))
    else:
        assert user_home is not None
        if "antigravity" in selected_platforms:
            for agy_dir in _discover_antigravity_targets(user_home):
                target_pairs.append(("antigravity", agy_dir))
        if "claude" in selected_platforms:
            for claude_dir in _discover_claude_targets(user_home):
                target_pairs.append(("claude", claude_dir))

    target_reports: list[dict[str, Any]] = []
    all_written: list[str] = []
    all_would_write: list[str] = []

    for plat, agents_dir in target_pairs:
        _assert_not_tracked_repo_target(agents_dir)
        allowed_names = _allowed_filenames_for(plat)
        files_map = rendered_by_platform[plat]
        resolved_agents_dir = agents_dir.resolve()

        written: list[str] = []
        would_write: list[str] = []
        unchanged: list[str] = []
        target_files: list[str] = []

        if not dry_run:
            agents_dir.mkdir(parents=True, exist_ok=True)
            resolved_agents_dir = agents_dir.resolve()

        for fname, content in sorted(files_map.items()):
            if fname not in allowed_names or "/" in fname or "\\" in fname or ".." in fname:
                raise ValueError(f"Refusing unexpected agent filename {fname!r} for {plat!r}")
            out_path = resolved_agents_dir / fname
            if _is_symlink_or_junction(out_path):
                raise ValueError(f"Refusing symlinked agent output file: {out_path}")
            resolved_out = out_path.resolve()
            if resolved_out.parent != resolved_agents_dir or resolved_out.name != fname:
                raise ValueError(f"Resolved output file escapes agents directory: {resolved_out}")

            target_files.append(str(resolved_out))
            norm_new = content.replace("\r\n", "\n").replace("\r", "\n")
            existing_text: str | None = None
            if out_path.exists():
                if out_path.is_dir():
                    raise ValueError(f"Agent output path is a directory, expected file: {out_path}")
                existing_text = out_path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")

            if existing_text == norm_new:
                unchanged.append(str(resolved_out))
                continue

            if dry_run:
                would_write.append(str(resolved_out))
                all_would_write.append(str(resolved_out))
            else:
                memory.atomic_write_text(out_path, norm_new)
                written.append(str(resolved_out))
                all_written.append(str(resolved_out))

        target_reports.append(
            {
                "platform": plat,
                "agents_dir": str(resolved_agents_dir),
                "files": target_files,
                "written": written,
                "would_write": would_write,
                "unchanged": unchanged,
            }
        )

    return {
        "action": "apply",
        "platform": platform,
        "dry_run": dry_run,
        "canonical_source": str(canonical_dir),
        "global_config_path": str(global_cfg_path),
        "global_config_exists": global_cfg_path.is_file(),
        "targets": target_reports,
        "written": all_written,
        "would_write": all_would_write,
    }
