"""Tests for canonical core/agents/*.md parsing, platform compilation, apply, and path safety."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from core.config import agents, models

REPO_ROOT = Path(__file__).resolve().parent.parent
CANONICAL_ROLES: tuple[str, ...] = (
    "generalist",
    "implementer",
    "ui",
    "planner",
    "builder",
    "verifier",
)
ANTIGRAVITY_FILENAMES = {
    "paddock-generalist.md",
    "paddock-implementer.md",
    "paddock-ui.md",
    "paddock-planner.md",
    "paddock-verifier.md",
}
CLAUDE_FILENAMES = {f"{role}.md" for role in CANONICAL_ROLES}


def _split_frontmatter(markdown: str) -> tuple[str, str]:
    assert "\r\n" not in markdown
    lines = markdown.split("\n")
    assert lines[0] == "---"
    end_idx = lines.index("---", 1)
    return "\n".join(lines[1:end_idx]), "\n".join(lines[end_idx + 1 :])


def _patch_symlink_paths(monkeypatch: pytest.MonkeyPatch, targets: set[Path]) -> None:
    """Force `_is_symlink_or_junction` to report `targets` as symlinks without OS privileges."""
    orig_is_symlink = Path.is_symlink

    def fake_is_symlink(self: Path) -> bool:
        if self in targets:
            return True
        return orig_is_symlink(self)

    monkeypatch.setattr(Path, "is_symlink", fake_is_symlink)


def _setup_fake_installs(fake_home: Path) -> tuple[Path, Path]:
    """Create simulated installed Antigravity and Claude Code plugin directories under `fake_home`."""
    agy_plugin = fake_home / ".gemini" / "config" / "plugins" / "paddock"
    (agy_plugin / "agents").mkdir(parents=True, exist_ok=True)

    claude_plugins = fake_home / ".claude" / "plugins"
    claude_plugin = claude_plugins / "cache" / "paddock" / "paddock" / "0.1.0"
    (claude_plugin / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (claude_plugin / ".claude-plugin" / "plugin.json").write_text(
        '{"name": "paddock"}\n', encoding="utf-8"
    )
    (claude_plugin / "agents").mkdir(parents=True, exist_ok=True)
    (claude_plugins / "installed_plugins.json").write_text(
        json.dumps({"plugins": {"paddock@paddock": [{"installPath": str(claude_plugin)}]}}),
        encoding="utf-8",
    )
    return agy_plugin / "agents", claude_plugin / "agents"


def test_parse_all_six_canonical_agents_and_reject_invalid_definitions() -> None:
    resolved_dir = agents.resolve_canonical_agents_dir()
    assert resolved_dir == (REPO_ROOT / "core" / "agents").resolve()

    loaded = agents.load_canonical_agents(REPO_ROOT)
    assert set(loaded) == set(CANONICAL_ROLES)

    assert loaded["generalist"].default_tier == "fast"
    assert loaded["generalist"].platforms == ("antigravity", "claude", "codex", "copilot", "pi")
    assert loaded["generalist"].capabilities == ("read", "edit", "write", "search", "web", "mcp")
    assert loaded["generalist"].disallowed == ("shell", "delegate")

    assert loaded["implementer"].default_tier == "workhorse"
    for mode in ("Mode: diagnose", "Mode: quick", "Mode: git"):
        assert mode in loaded["implementer"].body

    assert loaded["ui"].default_tier == "workhorse"
    assert "npx impeccable detect --json" in loaded["ui"].body

    assert loaded["planner"].default_tier == "workhorse"
    for marker in ("Agent:", "Files:", "Scope:", "Acceptance:", "preserve unaffected tasks"):
        assert marker in loaded["planner"].body

    assert loaded["builder"].default_tier == "workhorse"
    assert loaded["builder"].platforms == ("claude", "codex", "copilot", "pi")
    assert "antigravity" not in loaded["builder"].platforms
    assert "delegate" in loaded["builder"].capabilities
    for token in ("FAIL: impl", "DISPUTE", "FAIL: plan"):
        assert token in loaded["builder"].body

    assert loaded["verifier"].default_tier == "workhorse"
    assert loaded["verifier"].disallowed == ("edit", "write", "delegate")
    for verdict in ("PASS", "FAIL: impl", "FAIL: plan", "FAIL: spec", "FAIL: env"):
        assert verdict in loaded["verifier"].body

    # Reject malformed or unsafe canonical frontmatter
    valid_sample = (REPO_ROOT / "core" / "agents" / "implementer.md").read_text(encoding="utf-8")
    assert agents.parse_canonical_agent_text(valid_sample, file_stem="implementer").name == "implementer"

    with pytest.raises(ValueError, match="opening YAML"):
        agents.parse_canonical_agent_text("name: implementer\nBody.\n")
    with pytest.raises(ValueError, match="does not match filename stem"):
        agents.parse_canonical_agent_text(valid_sample, file_stem="verifier")

    invalid_samples = [
        # Missing required key
        "---\nname: implementer\ndescription: x\ndefault_tier: workhorse\nplatforms: [claude]\ncapabilities: [read]\n---\nBody.\n",
        # Unknown frontmatter key
        "---\nname: implementer\ndescription: x\ndefault_tier: workhorse\nplatforms: [claude]\ncapabilities: [read]\ndisallowed: []\nextra: 1\n---\nBody.\n",
        # Capability/disallowed overlap
        "---\nname: implementer\ndescription: x\ndefault_tier: workhorse\nplatforms: [claude]\ncapabilities: [read, shell]\ndisallowed: [shell]\n---\nBody.\n",
        # Unsafe YAML tag / prefix
        "---\nname: implementer\ndescription: !!python/object:os.system\ndefault_tier: workhorse\nplatforms: [claude]\ncapabilities: [read]\ndisallowed: []\n---\nBody.\n",
        # Unsupported YAML escape sequence
        '---\nname: implementer\ndescription: "bad\\nescape"\ndefault_tier: workhorse\nplatforms: [claude]\ncapabilities: [read]\ndisallowed: []\n---\nBody.\n',
        # Tab/control character in frontmatter
        "---\nname: implementer\ndescription: bad\tdesc\ndefault_tier: workhorse\nplatforms: [claude]\ncapabilities: [read]\ndisallowed: []\n---\nBody.\n",
        # Empty body
        "---\nname: implementer\ndescription: ok\ndefault_tier: workhorse\nplatforms: [claude]\ncapabilities: [read]\ndisallowed: []\n---\n   \n",
    ]
    for bad in invalid_samples:
        with pytest.raises(ValueError):
            agents.parse_canonical_agent_text(bad)


def test_capability_mapping_and_platform_compilation_and_footers() -> None:
    all_caps = ("read", "edit", "write", "shell", "tasks", "search", "web", "delegate", "mcp")
    assert agents.map_capabilities(all_caps, "antigravity") == [
        "read_file",
        "edit_file",
        "write_file",
        "run_command",
        "manage_task",
        "search_web",
        "call_mcp_tool",
    ]
    assert agents.map_capabilities(all_caps, "claude") == [
        "Read",
        "Edit",
        "Write",
        "Bash",
        "Grep",
        "Glob",
        "WebFetch",
        "WebSearch",
        "Agent",
        "mcp__plugin_paddock_paddock-worktree__worktree_create",
        "mcp__plugin_paddock_paddock-worktree__worktree_list",
        "mcp__plugin_paddock_paddock-worktree__worktree_remove",
    ]

    with pytest.raises(ValueError):
        agents.map_capabilities(["read"], "codex")
    with pytest.raises(ValueError):
        agents.map_capabilities(["unknown_cap"], "claude")

    rendered = agents.render_all_platforms(REPO_ROOT, models.default_config())
    assert set(rendered) == {"antigravity", "claude"}
    assert set(rendered["antigravity"]) == ANTIGRAVITY_FILENAMES
    assert set(rendered["claude"]) == CLAUDE_FILENAMES

    # Antigravity compilation checks
    agy_gen_fm, agy_gen_body = _split_frontmatter(rendered["antigravity"]["paddock-generalist.md"])
    assert "name: paddock-generalist" in agy_gen_fm
    assert "subagent: true" in agy_gen_fm
    assert "mainAgent: false" in agy_gen_fm
    assert "model:" not in agy_gen_fm
    assert "  - read_file\n  - edit_file\n  - write_file\n  - search_web\n  - call_mcp_tool" in agy_gen_fm
    assert "run_command" not in agy_gen_fm
    assert "Inside `run_workflow`, submit structured JSON via `finish`" in agy_gen_body
    assert "Standalone via `invoke_subagent`, end your reply with `## Evidence` and `## Unverified`." in agy_gen_body

    agy_ver_fm, agy_ver_body = _split_frontmatter(rendered["antigravity"]["paddock-verifier.md"])
    assert "  - read_file\n  - run_command\n  - manage_task" in agy_ver_fm
    assert "VERDICT_SCHEMA" in agy_ver_body
    assert "Standalone via `invoke_subagent`, include `## Evidence`, `## Unverified`" in agy_ver_body

    # Builder cannot be rendered for Antigravity
    loaded = agents.load_canonical_agents(REPO_ROOT)
    with pytest.raises(ValueError, match="does not target 'antigravity'"):
        agents.render_antigravity_agent(loaded["builder"])

    # Claude Code compilation checks
    cl_gen_fm, cl_gen_body = _split_frontmatter(rendered["claude"]["generalist.md"])
    cl_gen_lines = cl_gen_fm.splitlines()
    assert "name: generalist" in cl_gen_lines
    assert "model: haiku" in cl_gen_lines
    assert "effort: medium" in cl_gen_lines
    assert (
        "tools: Read, Edit, Write, Grep, Glob, WebFetch, WebSearch, "
        "mcp__plugin_paddock_paddock-worktree__worktree_create, "
        "mcp__plugin_paddock_paddock-worktree__worktree_list, "
        "mcp__plugin_paddock_paddock-worktree__worktree_remove"
    ) in cl_gen_lines
    assert "disallowedTools: Bash, Agent" in cl_gen_lines
    assert "End every reply with `## Evidence` and `## Unverified`." in cl_gen_body

    cl_bld_fm, _ = _split_frontmatter(rendered["claude"]["builder.md"])
    assert "model: sonnet" in cl_bld_fm
    assert "tools: Agent, Read, Grep, Glob" in cl_bld_fm
    assert "disallowedTools: Edit, Write, Bash" in cl_bld_fm

    cl_ver_fm, cl_ver_body = _split_frontmatter(rendered["claude"]["verifier.md"])
    assert "tools: Read, Bash, Grep, Glob" in cl_ver_fm
    assert "disallowedTools: Edit, Write, Agent" in cl_ver_fm
    assert "`PASS` | `FAIL: impl` | `FAIL: plan` | `FAIL: spec` | `FAIL: env`" in cl_ver_body

    with pytest.raises(ValueError):
        agents.render_claude_agent(loaded["implementer"], model="anthropic/sonnet", effort="medium")
    with pytest.raises(ValueError):
        agents.render_claude_agent(loaded["implementer"], model="sonnet", effort="invalid")


def test_apply_models_updates_installed_profiles_and_ignores_project_without_touching_repo(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("PADDOCK_HOME", raising=False)
    fake_home = tmp_path / "home"
    paddock_home = fake_home / ".paddock"
    repo_workspace = tmp_path / "workspace"
    repo_workspace.mkdir(parents=True)

    agy_dir, claude_dir = _setup_fake_installs(fake_home)
    (claude_dir / "implementer.md").write_text("stale\n", encoding="utf-8")
    (agy_dir / "paddock-implementer.md").write_text("stale\n", encoding="utf-8")

    # Configure global ~/.paddock/models.json and a DIFFERENT project override (must not leak into static global profiles)
    models.set_preset("quality", scope="global", cwd=repo_workspace, home=paddock_home)
    models.set_override(
        "claude", "generalist", model="haiku", effort="low", scope="project", cwd=repo_workspace, home=paddock_home
    )

    repo_claude_impl = (REPO_ROOT / "platforms" / "claude" / "agents" / "implementer.md").read_text(encoding="utf-8")
    repo_agy_impl = (REPO_ROOT / "platforms" / "antigravity" / "agents" / "paddock-implementer.md").read_text(
        encoding="utf-8"
    )

    # Dry run creates/changes nothing
    dry_res = agents.apply_models_to_targets(
        platform="all", cwd=repo_workspace, home=fake_home, dry_run=True
    )
    assert dry_res["dry_run"] is True
    assert dry_res["written"] == []
    assert len(dry_res["would_write"]) == 11
    assert (claude_dir / "implementer.md").read_text(encoding="utf-8") == "stale\n"
    assert (agy_dir / "paddock-implementer.md").read_text(encoding="utf-8") == "stale\n"

    # Real apply via CLI using --home (without PADDOCK_HOME set in env)
    env = os.environ.copy()
    env.pop("PADDOCK_HOME", None)
    proc = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "models.py"),
            "apply",
            "--platform",
            "all",
            "--cwd",
            str(repo_workspace),
            "--home",
            str(fake_home),
            "--json",
        ],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.returncode == 0, proc.stderr
    applied = json.loads(proc.stdout)
    assert len(applied["written"]) == 11
    assert {p.name for p in agy_dir.iterdir() if p.is_file()} == ANTIGRAVITY_FILENAMES
    assert {p.name for p in claude_dir.iterdir() if p.is_file()} == CLAUDE_FILENAMES

    # Installed Claude profiles reflect global quality preset, NOT project override on generalist
    installed_impl = (claude_dir / "implementer.md").read_text(encoding="utf-8")
    installed_ui = (claude_dir / "ui.md").read_text(encoding="utf-8")
    installed_gen = (claude_dir / "generalist.md").read_text(encoding="utf-8")
    assert "\nmodel: opus\n" in installed_impl
    assert "\neffort: medium\n" in installed_impl
    assert "\nmodel: sonnet\n" in installed_ui
    assert "\neffort: high\n" in installed_ui
    assert "\nmodel: haiku\n" in installed_gen
    assert "\neffort: medium\n" in installed_gen
    assert "\r\n" not in installed_impl

    # Second apply is idempotent (all unchanged)
    second = agents.apply_models_to_targets(platform="all", cwd=repo_workspace, home=fake_home)
    assert second["written"] == []
    assert all(len(t["unchanged"]) in (5, 6) for t in second["targets"])

    # Repo tracked files remain completely untouched
    assert (REPO_ROOT / "platforms" / "claude" / "agents" / "implementer.md").read_text(
        encoding="utf-8"
    ) == repo_claude_impl
    assert (REPO_ROOT / "platforms" / "antigravity" / "agents" / "paddock-implementer.md").read_text(
        encoding="utf-8"
    ) == repo_agy_impl


def test_standalone_paddock_core_agents_fallback_outside_repo(tmp_path: Path) -> None:
    # Simulate an installed Claude plugin completely isolated from the Paddock git checkout
    standalone_plugin = tmp_path / "isolated_plugin"
    paddock_core_dst = standalone_plugin / "scripts" / "paddock_core"
    shutil.copytree(
        REPO_ROOT / "platforms" / "claude" / "scripts" / "paddock_core",
        paddock_core_dst,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    assert not (standalone_plugin / "core").exists()

    paddock_home = tmp_path / "paddock_home"
    paddock_home.mkdir()
    (paddock_home / "models.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "preset": "budget",
                "overrides": {"claude": {"verifier": {"model": "opus", "effort": "high"}}},
            }
        ),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["PADDOCK_HOME"] = str(paddock_home)
    proc = subprocess.run(
        [
            sys.executable,
            str(paddock_core_dst / "models.py"),
            "apply",
            "--platform",
            "claude",
            "--target",
            str(standalone_plugin),
            "--json",
        ],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.returncode == 0, proc.stderr
    res = json.loads(proc.stdout)
    assert Path(res["canonical_source"]).resolve() == (paddock_core_dst / "agents").resolve()
    assert len(res["written"]) == 6

    impl_text = (standalone_plugin / "agents" / "implementer.md").read_text(encoding="utf-8")
    ver_text = (standalone_plugin / "agents" / "verifier.md").read_text(encoding="utf-8")
    assert "\nmodel: haiku\n" in impl_text
    assert "\neffort: high\n" in impl_text
    assert "\nmodel: opus\n" in ver_text
    assert "\neffort: high\n" in ver_text


def test_apply_refuses_tracked_repo_traversal_and_symlinks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_home = tmp_path / "home"
    agy_dir, claude_dir = _setup_fake_installs(fake_home)

    # --target with --platform all is rejected
    with pytest.raises(ValueError, match="single --platform"):
        agents.apply_models_to_targets(platform="all", target=tmp_path / "custom")

    # Refuses writing into tracked repo directories or a git-tracked temp repo
    with pytest.raises(ValueError, match="tracked Paddock repository"):
        agents.apply_models_to_targets(
            platform="claude",
            home=fake_home,
            target=REPO_ROOT / "platforms" / "claude",
        )

    git_repo = tmp_path / "tracked_repo"
    (git_repo / "agents").mkdir(parents=True)
    subprocess.run(["git", "-C", str(git_repo), "init", "-b", "main"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(git_repo), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(git_repo), "config", "user.email", "t@example.com"], check=True)
    (git_repo / "agents" / "implementer.md").write_text("tracked\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(git_repo), "add", "agents/implementer.md"], check=True)
    subprocess.run(["git", "-C", str(git_repo), "commit", "-m", "init"], check=True, capture_output=True)
    with pytest.raises(ValueError, match="git-tracked"):
        agents.apply_models_to_targets(platform="claude", home=fake_home, target=git_repo)

    # Reject `..` path traversal across parameters and installed_plugins.json
    for bad_target in ("../escape", str(tmp_path / "a" / ".." / "b")):
        with pytest.raises(ValueError, match="Path traversal"):
            agents.apply_models_to_targets(platform="claude", home=fake_home, target=bad_target)
    with pytest.raises(ValueError, match="Path traversal"):
        agents.apply_models_to_targets(platform="all", home=str(tmp_path / "a" / ".." / "home"))
    with pytest.raises(ValueError, match="Path traversal"):
        agents.apply_models_to_targets(
            platform="claude", home=fake_home, target=tmp_path / "ok", paddock_home="../ph"
        )
    with pytest.raises(ValueError, match="Path traversal"):
        agents.resolve_canonical_agents_dir(str(tmp_path / "a" / ".." / "src"))

    # CLI traversal refusal exits non-zero
    proc = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "models.py"),
            "apply",
            "--platform",
            "claude",
            "--target",
            "../escape",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.returncode != 0
    assert "Path traversal" in proc.stderr

    # Claude installed_plugins.json installPath traversal or escape out of plugins root is rejected
    installed_json = fake_home / ".claude" / "plugins" / "installed_plugins.json"
    orig_installed = installed_json.read_text(encoding="utf-8")
    outside_dir = tmp_path / "outside_plugin"
    (outside_dir / "agents").mkdir(parents=True)
    installed_json.write_text(
        json.dumps({"plugins": {"paddock@paddock": [{"installPath": str(outside_dir)}]}}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="escapes"):
        agents.apply_models_to_targets(platform="claude", home=fake_home)

    installed_json.write_text(
        json.dumps({"plugins": {"paddock@paddock": [{"installPath": "../../escape"}]}}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Path traversal"):
        agents.apply_models_to_targets(platform="claude", home=fake_home)
    installed_json.write_text(orig_installed, encoding="utf-8")

    # Symlink refusal on target, output file, canonical file, and discovered plugin dirs
    custom_target = tmp_path / "custom_target"
    custom_target.mkdir()
    _patch_symlink_paths(monkeypatch, {custom_target / "agents"})
    with pytest.raises(ValueError, match="symlink"):
        agents.apply_models_to_targets(platform="claude", home=fake_home, target=custom_target)

    _patch_symlink_paths(monkeypatch, {claude_dir / "implementer.md"})
    with pytest.raises(ValueError, match="symlink"):
        agents.apply_models_to_targets(platform="claude", home=fake_home)

    _patch_symlink_paths(monkeypatch, {agy_dir})
    with pytest.raises(ValueError, match="symlink"):
        agents.apply_models_to_targets(platform="antigravity", home=fake_home)

    canon_copy = tmp_path / "canon_copy"
    shutil.copytree(REPO_ROOT / "core" / "agents", canon_copy)
    _patch_symlink_paths(monkeypatch, {canon_copy / "implementer.md"})
    with pytest.raises(ValueError, match="symlink"):
        agents.load_canonical_agents(canon_copy)
