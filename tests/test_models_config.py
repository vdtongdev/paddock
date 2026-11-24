"""Unit and CLI tests for core/config/models.py, scripts/models.py, and synced paddock_core/models.py."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from core.config import models
from core.memory import memory

REPO_ROOT = Path(__file__).resolve().parent.parent
MODEL_CLIS: tuple[Path, ...] = (
    REPO_ROOT / "scripts" / "models.py",
    REPO_ROOT / "platforms" / "antigravity" / "scripts" / "paddock_core" / "models.py",
    REPO_ROOT / "platforms" / "claude" / "scripts" / "paddock_core" / "models.py",
)


def _run_cli(
    cli_path: Path,
    args: list[str],
    paddock_home: Path,
    *,
    cwd: Path | None = None,
    expect_ok: bool = True,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PADDOCK_HOME"] = str(paddock_home)
    proc = subprocess.run(
        [sys.executable, str(cli_path), *args],
        cwd=str(cwd or REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if expect_ok:
        assert proc.returncode == 0, f"{cli_path} {args} failed: {proc.stderr}"
    else:
        assert proc.returncode != 0, f"{cli_path} {args} unexpectedly succeeded: {proc.stdout}"
    return proc


def _init_git_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(repo), "init", "-b", "main"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@example.com"], check=True)
    (repo / "README.md").write_text("hi\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "init"], check=True, capture_output=True)


def _patch_symlink_path(monkeypatch: pytest.MonkeyPatch, target: Path) -> None:
    """Force `_is_symlink_or_junction` to see `target` as a symlink without OS privileges."""
    orig_is_symlink = Path.is_symlink

    def fake_is_symlink(self: Path) -> bool:
        if self == target:
            return True
        return orig_is_symlink(self)

    monkeypatch.setattr(Path, "is_symlink", fake_is_symlink)


def test_schema_version_validation_and_defaults() -> None:
    cfg = models.default_config()
    assert cfg["schema_version"] == 1
    assert cfg["preset"] == "balanced"
    assert models.validate_config(cfg) == cfg

    for bad_cfg in (
        [],
        "not-a-dict",
        {},
        {"schema_version": 0},
        {"schema_version": 2},
        {"schema_version": "1"},
        {"schema_version": True},
        {"schema_version": 1, "unknown_key": 1},
        {"schema_version": 1, "tiers": []},
        {"schema_version": 1, "roles": []},
        {"schema_version": 1, "overrides": []},
        {"schema_version": 1, "roles": {"implementer": {"tier": "fast", "extra": 1}}},
        {"schema_version": 1, "roles": {"implementer": {"effort": "high"}}},
        {"schema_version": 1, "tiers": {"claude": {"fast": {"effort": "medium"}}}},
        {"schema_version": 1, "overrides": {"claude": {"implementer": {}}}},
        {"schema_version": 1, "overrides": {"claude": {"implementer": {"extra": "x"}}}},
    ):
        with pytest.raises(ValueError):
            models.validate_config(bad_cfg)


def test_default_medium_effort_and_anthropic_defaults_across_platforms(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PADDOCK_HOME", str(tmp_path / "paddock"))
    summary = models.resolve_all(tmp_path)

    assert summary["schema_version"] == 1
    assert summary["effective_preset"] == "balanced"
    assert models.DEFAULT_PROVIDERS == {
        "antigravity": "google",
        "claude": "anthropic",
        "codex": "openai",
        "copilot": "anthropic",
        "pi": "anthropic",
    }

    expected_balanced = {
        "antigravity": ("gemini-4-hydrogen", "gemini-4-neon"),
        "claude": ("haiku", "sonnet"),
        "codex": ("gpt-6-luna", "gpt-6.2-sol"),
        "copilot": ("claude-haiku-5.5", "claude-sonnet-5.5"),
        "pi": ("anthropic/claude-haiku-5-5", "anthropic/claude-sonnet-5-5"),
    }
    for plat, (fast_model, workhorse_model) in expected_balanced.items():
        roles = summary["platforms"][plat]
        for role in models.ROLES:
            expected_model = fast_model if role == "generalist" else workhorse_model
            expected_tier = "fast" if role == "generalist" else "workhorse"
            assert roles[role]["model"] == expected_model
            assert roles[role]["effort"] == "medium"
            assert roles[role]["tier"] == expected_tier
            assert roles[role]["source"] == "default"

    # Anthropic defaults on copilot and pi include flagship as well
    models.set_preset("quality", scope="global", cwd=tmp_path)
    quality = models.resolve_all(tmp_path)
    assert quality["platforms"]["copilot"]["implementer"]["model"] == "claude-opus-5.5"
    assert quality["platforms"]["pi"]["implementer"]["model"] == "anthropic/claude-opus-5-5"


@pytest.mark.parametrize(
    ("preset", "expected_role_specs"),
    [
        (
            "budget",
            {
                "generalist": ("fast", "medium"),
                "implementer": ("fast", "high"),
                "ui": ("workhorse", "low"),
                "planner": ("workhorse", "low"),
                "builder": ("workhorse", "low"),
                "verifier": ("fast", "high"),
            },
        ),
        (
            "balanced",
            {
                "generalist": ("fast", "medium"),
                "implementer": ("workhorse", "medium"),
                "ui": ("workhorse", "medium"),
                "planner": ("workhorse", "medium"),
                "builder": ("workhorse", "medium"),
                "verifier": ("workhorse", "medium"),
            },
        ),
        (
            "quality",
            {
                "generalist": ("fast", "medium"),
                "implementer": ("flagship", "medium"),
                "ui": ("workhorse", "high"),
                "planner": ("flagship", "medium"),
                "builder": ("workhorse", "medium"),
                "verifier": ("flagship", "medium"),
            },
        ),
    ],
)
def test_presets_across_all_five_platforms(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    preset: str,
    expected_role_specs: dict[str, tuple[str, str]],
) -> None:
    monkeypatch.setenv("PADDOCK_HOME", str(tmp_path / "paddock"))
    models.set_preset(preset, scope="global", cwd=tmp_path)
    summary = models.resolve_all(tmp_path)
    assert summary["effective_preset"] == preset

    tier_models = {
        plat: {
            tier: models.PROVIDER_CATALOGS[plat][models.DEFAULT_PROVIDERS[plat]][tier]["model"]
            for tier in models.TIERS
        }
        for plat in models.PLATFORMS
    }

    for plat in models.PLATFORMS:
        for role, (tier, effort) in expected_role_specs.items():
            resolved = summary["platforms"][plat][role]
            expected_model = tier_models[plat][tier]
            assert resolved["model"] == expected_model
            assert resolved["effort"] == effort
            assert resolved["tier"] == tier
            assert resolved["source"] == "global-tier"
            if plat == "antigravity":
                assert resolved["swarm_model"] == f"{expected_model}-{effort}"
                assert resolved["subagent_alias"] == models.TIER_TO_ANTIGRAVITY_ALIAS[tier]

    # Explicit concrete Gemini 4 assertions on Antigravity default google catalog
    agy = summary["platforms"]["antigravity"]
    if preset == "budget":
        assert agy["generalist"]["swarm_model"] == "gemini-4-hydrogen-medium"
        assert agy["implementer"]["swarm_model"] == "gemini-4-hydrogen-high"
        assert agy["verifier"]["swarm_model"] == "gemini-4-hydrogen-high"
        assert agy["planner"]["swarm_model"] == "gemini-4-neon-low"
        assert agy["ui"]["swarm_model"] == "gemini-4-neon-low"
        assert agy["builder"]["swarm_model"] == "gemini-4-neon-low"
    elif preset == "balanced":
        assert agy["generalist"]["swarm_model"] == "gemini-4-hydrogen-medium"
        for role in ("implementer", "ui", "planner", "builder", "verifier"):
            assert agy[role]["swarm_model"] == "gemini-4-neon-medium"
    else:
        assert agy["generalist"]["swarm_model"] == "gemini-4-hydrogen-medium"
        assert agy["ui"]["swarm_model"] == "gemini-4-neon-high"
        assert agy["builder"]["swarm_model"] == "gemini-4-neon-medium"
        for role in ("implementer", "planner", "verifier"):
            assert agy[role]["swarm_model"] == "gemini-4-carbon-medium"


def test_bare_antigravity_aliases_cleanly_ignore_effort(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PADDOCK_HOME", str(tmp_path / "paddock"))
    models.set_preset("budget", scope="global", cwd=tmp_path)
    models.set_provider("antigravity", "native", scope="global", cwd=tmp_path)

    agy = models.resolve_platform("antigravity", tmp_path)
    assert agy["generalist"]["model"] == "flash"
    assert agy["implementer"]["model"] == "flash"
    assert agy["planner"]["model"] == "pro"
    for role in models.ROLES:
        assert agy[role]["effort"] is None
        assert agy[role]["swarm_model"] == agy[role]["model"]
        assert agy[role]["subagent_alias"] == agy[role]["model"]

    # Role-level effort and flash_lite override and call-time effort also ignore effort cleanly
    models.set_role("implementer", "flagship", effort="high", scope="global", cwd=tmp_path)
    res_inherit = models.resolve_role("antigravity", "implementer", tmp_path)
    assert res_inherit["model"] == "inherit"
    assert res_inherit["effort"] is None
    assert res_inherit["swarm_model"] == "inherit"
    assert res_inherit["subagent_alias"] == "inherit"

    models.set_override("antigravity", "ui", model="flash_lite", scope="project", cwd=tmp_path)
    res_lite = models.resolve_role("antigravity", "ui", tmp_path)
    assert res_lite["model"] == "flash_lite"
    assert res_lite["effort"] is None
    assert res_lite["swarm_model"] == "flash_lite"
    assert res_lite["subagent_alias"] == "flash_lite"

    res_call = models.resolve_role("antigravity", "ui", tmp_path, call_time_effort="low")
    assert res_call["model"] == "flash_lite"
    assert res_call["effort"] is None
    assert res_call["swarm_model"] == "flash_lite"


def test_set_provider_switching_reprojects_active_presets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PADDOCK_HOME", str(tmp_path / "paddock"))
    models.set_preset("budget", scope="global", cwd=tmp_path)

    # Antigravity: google -> anthropic -> native -> google
    models.set_provider("antigravity", "anthropic", scope="global", cwd=tmp_path)
    agy_anth = models.resolve_platform("antigravity", tmp_path)
    assert agy_anth["generalist"]["swarm_model"] == "gemini-4-hydrogen-medium"
    assert agy_anth["implementer"]["swarm_model"] == "gemini-4-hydrogen-high"
    assert agy_anth["planner"]["swarm_model"] == "claude-sonnet-5-5-low"
    assert agy_anth["planner"]["subagent_alias"] == "pro"

    models.set_preset("quality", scope="global", cwd=tmp_path)
    agy_anth_q = models.resolve_platform("antigravity", tmp_path)
    assert agy_anth_q["implementer"]["swarm_model"] == "claude-opus-5-5-medium"
    assert agy_anth_q["implementer"]["subagent_alias"] == "inherit"
    assert agy_anth_q["ui"]["swarm_model"] == "claude-sonnet-5-5-high"

    models.set_provider("antigravity", "native", scope="global", cwd=tmp_path)
    agy_nat = models.resolve_platform("antigravity", tmp_path)
    assert agy_nat["implementer"]["model"] == "inherit"
    assert agy_nat["implementer"]["effort"] is None
    assert agy_nat["ui"]["model"] == "pro"
    assert agy_nat["ui"]["effort"] is None

    models.set_provider("antigravity", "google", scope="global", cwd=tmp_path)
    agy_goog = models.resolve_platform("antigravity", tmp_path)
    assert agy_goog["implementer"]["swarm_model"] == "gemini-4-carbon-medium"
    assert agy_goog["ui"]["swarm_model"] == "gemini-4-neon-high"

    # Copilot & Pi: anthropic -> openai -> google -> anthropic under budget preset
    models.set_preset("budget", scope="global", cwd=tmp_path)
    for plat, prefix_map in (
        ("copilot", {"openai": "", "google": "", "anthropic": ""}),
        ("pi", {"openai": "openai/", "google": "google/", "anthropic": "anthropic/"}),
    ):
        models.set_provider(plat, "openai", scope="global", cwd=tmp_path)
        res_oai = models.resolve_platform(plat, tmp_path)
        assert res_oai["implementer"]["model"] == f"{prefix_map['openai']}gpt-6-luna"
        assert res_oai["implementer"]["effort"] == "high"
        assert res_oai["planner"]["model"] == f"{prefix_map['openai']}gpt-6.2-sol"
        assert res_oai["planner"]["effort"] == "low"

        models.set_provider(plat, "google", scope="global", cwd=tmp_path)
        res_goog = models.resolve_platform(plat, tmp_path)
        assert res_goog["implementer"]["model"] == f"{prefix_map['google']}gemini-4-hydrogen"
        assert res_goog["implementer"]["effort"] == "high"
        assert res_goog["planner"]["model"] == f"{prefix_map['google']}gemini-4-neon"
        assert res_goog["planner"]["effort"] == "low"

        models.set_provider(plat, "anthropic", scope="global", cwd=tmp_path)
        res_anth = models.resolve_platform(plat, tmp_path)
        expected_fast = (
            "anthropic/claude-haiku-5-5" if plat == "pi" else "claude-haiku-5.5"
        )
        expected_work = (
            "anthropic/claude-sonnet-5-5" if plat == "pi" else "claude-sonnet-5.5"
        )
        assert res_anth["implementer"]["model"] == expected_fast
        assert res_anth["implementer"]["effort"] == "high"
        assert res_anth["planner"]["model"] == expected_work
        assert res_anth["planner"]["effort"] == "low"


def test_swarm_model_and_antigravity_subagent_alias_resolution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PADDOCK_HOME", str(tmp_path / "paddock"))

    assert models.antigravity_subagent_alias(tier="fast") == "flash"
    assert models.antigravity_subagent_alias(tier="workhorse") == "pro"
    assert models.antigravity_subagent_alias(tier="flagship") == "inherit"
    for bare in ("inherit", "flash_lite", "flash", "pro"):
        assert models.antigravity_subagent_alias(model=bare, tier="workhorse") == bare

    assert models.antigravity_subagent_alias(model="gemini-4-hydrogen") == "flash"
    assert models.antigravity_subagent_alias(model="claude-sonnet-5-5") == "pro"
    assert models.antigravity_subagent_alias(model="claude-opus-5-5") == "inherit"
    assert models.antigravity_subagent_alias(model="custom-model-v1") == "pro"

    assert models.format_antigravity_swarm_model("gemini-4-neon", "high") == "gemini-4-neon-high"
    assert models.format_antigravity_swarm_model("gemini-4-neon", None) == "gemini-4-neon-medium"
    assert models.format_antigravity_swarm_model("flash_lite", "high") == "flash_lite"

    models.set_override(
        "antigravity", "implementer", model="claude-opus-5-5", effort="high", scope="project", cwd=tmp_path
    )
    resolved = models.resolve_role("antigravity", "implementer", tmp_path)
    assert resolved["swarm_model"] == "claude-opus-5-5-high"
    assert resolved["subagent_alias"] == "inherit"


def test_string_vs_dict_role_entries(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PADDOCK_HOME", str(tmp_path / "paddock"))

    cfg = {
        "schema_version": 1,
        "tiers": {
            "claude": {"fast": {"model": "haiku", "effort": "low"}},
        },
        "roles": {
            "generalist": "fast",
            "implementer": {"tier": "fast", "effort": "high"},
            "ui": {"tier": "workhorse"},
        },
    }
    validated = models.validate_config(cfg)
    assert validated["roles"]["generalist"] == "fast"
    assert validated["roles"]["implementer"] == {"tier": "fast", "effort": "high"}
    assert validated["roles"]["ui"] == "workhorse"

    models.config_path("global", tmp_path).parent.mkdir(parents=True, exist_ok=True)
    models.config_path("global", tmp_path).write_text(json.dumps(cfg), encoding="utf-8")

    # String role inherits tier's effort ("low"); dict role overrides effort ("high")
    gen = models.resolve_role("claude", "generalist", tmp_path)
    assert gen["model"] == "haiku"
    assert gen["effort"] == "low"
    assert gen["tier"] == "fast"

    impl = models.resolve_role("claude", "implementer", tmp_path)
    assert impl["model"] == "haiku"
    assert impl["effort"] == "high"
    assert impl["tier"] == "fast"

    agy_impl = models.resolve_role("antigravity", "implementer", tmp_path)
    assert agy_impl["swarm_model"] == "gemini-4-hydrogen-high"
    assert agy_impl["subagent_alias"] == "flash"


def test_six_step_precedence_order(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PADDOCK_HOME", str(tmp_path / "paddock"))

    # Step 6: default
    r6 = models.resolve_role("claude", "implementer", tmp_path)
    assert (r6["model"], r6["effort"], r6["source"]) == ("sonnet", "medium", "default")

    # Step 5: global-tier beats default
    models.set_role("implementer", "fast", effort="low", scope="global", cwd=tmp_path)
    r5 = models.resolve_role("claude", "implementer", tmp_path)
    assert (r5["model"], r5["effort"], r5["source"]) == ("haiku", "low", "global-tier")

    # Step 4: global-override beats global-tier
    models.set_override("claude", "implementer", model="sonnet", effort="high", scope="global", cwd=tmp_path)
    r4 = models.resolve_role("claude", "implementer", tmp_path)
    assert (r4["model"], r4["effort"], r4["source"]) == ("sonnet", "high", "global-override")

    # Step 3: project-tier beats global-override and global-tier
    models.set_role("implementer", "flagship", effort="low", scope="project", cwd=tmp_path)
    r3 = models.resolve_role("claude", "implementer", tmp_path)
    assert (r3["model"], r3["effort"], r3["source"]) == ("opus", "low", "project-tier")

    # Step 2: project-override beats project-tier
    models.set_override("claude", "implementer", model="haiku", effort="high", scope="project", cwd=tmp_path)
    r2 = models.resolve_role("claude", "implementer", tmp_path)
    assert (r2["model"], r2["effort"], r2["source"]) == ("haiku", "high", "project-override")

    # Step 1: call-time beats project-override
    r1 = models.resolve_role(
        "claude",
        "implementer",
        tmp_path,
        call_time_model="claude-opus-5-5",
        call_time_effort="medium",
    )
    assert (r1["model"], r1["effort"], r1["source"]) == ("claude-opus-5-5", "medium", "call-time")

    # Effort-only call-time inherits project-override model
    r1_eff = models.resolve_role("claude", "implementer", tmp_path, call_time_effort="low")
    assert (r1_eff["model"], r1_eff["effort"], r1_eff["source"]) == ("haiku", "low", "call-time")


def test_partial_project_merge_and_worktree_shared_slug(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PADDOCK_HOME", str(tmp_path / "paddock"))
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    wt = tmp_path / "wt-feature"
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "-b", "feat/models", str(wt), "main"],
        check=True,
        capture_output=True,
    )

    models.set_preset("quality", scope="global", cwd=repo)
    models.set_override("claude", "verifier", model="sonnet", effort="low", scope="global", cwd=repo)

    # Write a minimal partial project JSON from the main checkout
    proj_path = models.config_path("project", repo)
    proj_path.parent.mkdir(parents=True, exist_ok=True)
    proj_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "roles": {"ui": {"tier": "fast", "effort": "low"}},
            }
        ),
        encoding="utf-8",
    )

    assert memory.repo_slug(repo) == memory.repo_slug(wt)
    assert models.config_path("project", repo) == models.config_path("project", wt)

    from_wt = models.resolve_all(wt)
    assert from_wt["repo_slug"] == memory.repo_slug(repo)
    assert from_wt["effective_preset"] == "quality"
    # Partial project role overrides ui, while global quality roles and global override remain intact
    assert from_wt["platforms"]["claude"]["ui"] == {
        "model": "haiku",
        "effort": "low",
        "tier": "fast",
        "source": "project-tier",
    }
    assert from_wt["platforms"]["claude"]["implementer"] == {
        "model": "opus",
        "effort": "medium",
        "tier": "flagship",
        "source": "global-tier",
    }
    assert from_wt["platforms"]["claude"]["verifier"] == {
        "model": "sonnet",
        "effort": "low",
        "tier": None,
        "source": "global-override",
    }


def test_strict_validation_rejects_invalid_inputs_and_traversal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PADDOCK_HOME", str(tmp_path / "paddock"))

    with pytest.raises(ValueError):
        models.validate_platform("vscode")
    with pytest.raises(ValueError):
        models.validate_provider("claude", "google")
    with pytest.raises(ValueError):
        models.validate_provider("codex", "anthropic")
    with pytest.raises(ValueError):
        models.validate_provider("antigravity", "openai")
    with pytest.raises(ValueError):
        models.validate_role("paddock-implementer")
    with pytest.raises(ValueError):
        models.validate_tier("ultra")
    with pytest.raises(ValueError):
        models.validate_preset("turbo")
    with pytest.raises(ValueError):
        models.validate_effort("xhigh")

    # Multi-provider `<provider>/<model-id>` accepted on pi/copilot/codex, rejected on claude/antigravity
    assert models.validate_model_id("pi", "anthropic/claude-sonnet-5-5", "high") == (
        "anthropic/claude-sonnet-5-5",
        "high",
    )
    assert models.validate_model_id("copilot", "openai/gpt-6.2-sol", "medium") == (
        "openai/gpt-6.2-sol",
        "medium",
    )
    for single_plat in ("claude", "antigravity"):
        with pytest.raises(ValueError):
            models.validate_model_id(single_plat, "anthropic/claude-sonnet-5-5")

    # Explicit effort rejected on bare Antigravity aliases, allowed on concrete Antigravity models
    for bare in ("inherit", "flash_lite", "flash", "pro"):
        assert models.validate_model_id("antigravity", bare) == (bare, None)
        with pytest.raises(ValueError, match="bare Antigravity alias"):
            models.validate_model_id("antigravity", bare, "medium")
        with pytest.raises(ValueError, match="bare Antigravity alias"):
            models.set_tier("antigravity", "fast", bare, effort="low", cwd=tmp_path)
        with pytest.raises(ValueError, match="bare Antigravity alias"):
            models.set_override("antigravity", "implementer", model=bare, effort="high", cwd=tmp_path)
        with pytest.raises(ValueError, match="bare Antigravity alias"):
            models.resolve_role("antigravity", "implementer", tmp_path, call_time_model=bare, call_time_effort="low")

    assert models.validate_model_id("antigravity", "gemini-4-neon", "high") == (
        "gemini-4-neon",
        "high",
    )

    # Reject traversal, multi-slash, newlines, quotes, YAML/shell metacharacters
    for bad_model in (
        "",
        "x",
        "../escape",
        "anthropic/../escape",
        "a/b/c",
        "/leading",
        "trailing/",
        "model\nevil",
        "model\revil",
        "model\x00null",
        "model\ttab",
        'model"quote',
        "model'quote",
        "model: colon",
        "model #comment",
        "model!tag",
        "model&anchor",
        "model*alias",
        "model$var",
        "model;cmd",
        "model|pipe",
        "model`tick",
        "model space",
        "UppercaseModel",
    ):
        for plat in ("antigravity", "claude", "pi"):
            with pytest.raises(ValueError):
                models.validate_model_id(plat, bad_model)

    # Path traversal and symlink checks for config_path
    with pytest.raises(ValueError):
        models.config_path("invalid", tmp_path)
    for bad_slug in ("", ".", "..", "../escape", "sub/dir", r"sub\dir"):
        with pytest.raises(ValueError):
            models._validate_slug(bad_slug)

    paddock_root = tmp_path / "paddock"
    paddock_root.mkdir()
    _patch_symlink_path(monkeypatch, paddock_root)
    with pytest.raises(ValueError, match="symlink"):
        models.config_path("global", tmp_path)

    paddock_root2 = tmp_path / "paddock2"
    paddock_root2.mkdir()
    monkeypatch.setenv("PADDOCK_HOME", str(paddock_root2))
    _patch_symlink_path(monkeypatch, paddock_root2 / "models.json")
    with pytest.raises(ValueError, match="symlink"):
        models.config_path("global", tmp_path)


@pytest.mark.parametrize("cli_path", MODEL_CLIS, ids=lambda p: p.relative_to(REPO_ROOT).as_posix())
def test_cli_subcommands_parity_across_scripts_and_synced_copies(
    tmp_path: Path, cli_path: Path
) -> None:
    paddock_home = tmp_path / "paddock"
    repo = tmp_path / "repo"
    repo.mkdir()

    # show (default)
    show_default = _run_cli(
        cli_path,
        ["show", "--cwd", str(repo), "--platform", "antigravity", "--json"],
        paddock_home,
    )
    data_default = json.loads(show_default.stdout)
    assert set(data_default["platforms"]) == {"antigravity"}
    assert data_default["platforms"]["antigravity"]["implementer"]["swarm_model"] == "gemini-4-neon-medium"
    assert data_default["platforms"]["antigravity"]["implementer"]["subagent_alias"] == "pro"

    # show human-readable output prints swarm_model and subagent_alias
    show_text = _run_cli(
        cli_path,
        ["show", "--cwd", str(repo), "--platform", "antigravity"],
        paddock_home,
    )
    assert "swarm_model" in show_text.stdout
    assert "subagent_alias" in show_text.stdout
    assert "gemini-4-neon-medium" in show_text.stdout

    # preset
    _run_cli(cli_path, ["preset", "budget", "--scope", "global", "--cwd", str(repo)], paddock_home)
    # set-provider
    _run_cli(
        cli_path,
        ["set-provider", "pi", "google", "--scope", "global", "--cwd", str(repo)],
        paddock_home,
    )
    # set-role --effort
    _run_cli(
        cli_path,
        ["set-role", "ui", "flagship", "--effort", "high", "--scope", "project", "--cwd", str(repo)],
        paddock_home,
    )
    # set-tier
    _run_cli(
        cli_path,
        [
            "set-tier",
            "copilot",
            "fast",
            "--model",
            "openai/gpt-6-luna",
            "--effort",
            "low",
            "--scope",
            "global",
            "--cwd",
            str(repo),
        ],
        paddock_home,
    )
    # set-override
    _run_cli(
        cli_path,
        [
            "set-override",
            "claude",
            "verifier",
            "--model",
            "opus",
            "--effort",
            "high",
            "--scope",
            "project",
            "--cwd",
            str(repo),
        ],
        paddock_home,
    )

    show_after = json.loads(
        _run_cli(cli_path, ["show", "--cwd", str(repo), "--json"], paddock_home).stdout
    )
    assert show_after["effective_preset"] == "budget"
    assert show_after["platforms"]["pi"]["implementer"]["model"] == "google/gemini-4-hydrogen"
    assert show_after["platforms"]["pi"]["implementer"]["effort"] == "high"
    assert show_after["platforms"]["antigravity"]["ui"]["swarm_model"] == "gemini-4-carbon-high"
    assert show_after["platforms"]["antigravity"]["ui"]["source"] == "project-tier"
    assert show_after["platforms"]["copilot"]["generalist"]["model"] == "openai/gpt-6-luna"
    assert show_after["platforms"]["copilot"]["generalist"]["effort"] == "low"
    assert show_after["platforms"]["claude"]["verifier"] == {
        "model": "opus",
        "effort": "high",
        "tier": None,
        "source": "project-override",
    }

    # set-override --clear
    _run_cli(
        cli_path,
        ["set-override", "claude", "verifier", "--clear", "--scope", "project", "--cwd", str(repo)],
        paddock_home,
    )
    show_cleared = json.loads(
        _run_cli(cli_path, ["show", "--cwd", str(repo), "--platform", "claude", "--json"], paddock_home).stdout
    )
    assert show_cleared["platforms"]["claude"]["verifier"] == {
        "model": "haiku",
        "effort": "high",
        "tier": "fast",
        "source": "global-tier",
    }

    # Invalid CLI rejections exit non-zero
    err_proc = _run_cli(
        cli_path,
        ["set-tier", "antigravity", "fast", "--model", "pro", "--effort", "high", "--cwd", str(repo)],
        paddock_home,
        expect_ok=False,
    )
    assert "bare Antigravity alias" in err_proc.stderr

    # reset
    _run_cli(cli_path, ["reset", "--scope", "all", "--cwd", str(repo)], paddock_home)
    show_reset = json.loads(
        _run_cli(cli_path, ["show", "--cwd", str(repo), "--json"], paddock_home).stdout
    )
    assert show_reset["global_exists"] is False
    assert show_reset["project_exists"] is False
    assert show_reset["effective_preset"] == "balanced"


def test_atomic_writes_and_corrupt_or_mismatched_json_fail_open(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    paddock_home = tmp_path / "paddock"
    monkeypatch.setenv("PADDOCK_HOME", str(paddock_home))

    models.set_preset("quality", scope="global", cwd=tmp_path)
    global_path = models.config_path("global", tmp_path)
    raw_bytes = global_path.read_bytes()
    assert b"\r\n" not in raw_bytes
    assert raw_bytes.endswith(b"\n")
    assert [p.name for p in global_path.parent.iterdir()] == ["models.json"]

    # Corrupt JSON in global config fails open to balanced defaults with warning on stderr
    global_path.write_text("{corrupt json", encoding="utf-8")
    summary = models.resolve_all(tmp_path)
    captured = capsys.readouterr()
    assert "ignoring invalid global config" in captured.err
    assert summary["effective_preset"] == "balanced"
    assert summary["platforms"]["claude"]["implementer"]["model"] == "sonnet"
    assert summary["platforms"]["claude"]["implementer"]["source"] == "default"

    # Corrupt global config must not be silently overwritten by mutating writes
    with pytest.raises(json.JSONDecodeError):
        models.set_preset("quality", scope="global", cwd=tmp_path)
    assert global_path.read_text(encoding="utf-8") == "{corrupt json"

    # Reset corrupt global config, then verify schema-mismatched project config fails open
    models.reset_config(scope="global", cwd=tmp_path)
    models.set_preset("quality", scope="global", cwd=tmp_path)
    proj_path = models.config_path("project", tmp_path)
    proj_path.parent.mkdir(parents=True, exist_ok=True)
    proj_path.write_text(json.dumps({"schema_version": 99, "preset": "budget"}), encoding="utf-8")

    summary2 = models.resolve_all(tmp_path)
    captured2 = capsys.readouterr()
    assert "ignoring invalid project config" in captured2.err
    assert summary2["platforms"]["claude"]["implementer"]["model"] == "opus"
    assert summary2["platforms"]["claude"]["implementer"]["source"] == "global-tier"

    # Corrupt project config must not be silently overwritten by mutating writes
    with pytest.raises(ValueError):
        models.set_preset("budget", scope="project", cwd=tmp_path)
    assert json.loads(proj_path.read_text(encoding="utf-8"))["schema_version"] == 99

    # CLI `show` also fails open cleanly (exit 0) on corrupt JSON
    proc = _run_cli(MODEL_CLIS[0], ["show", "--cwd", str(tmp_path), "--json"], paddock_home)
    assert "ignoring invalid project config" in proc.stderr
    assert json.loads(proc.stdout)["platforms"]["claude"]["implementer"]["model"] == "opus"
