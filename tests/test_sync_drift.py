"""Tests for core skill/module/agent sync parity, drift detection, and installer behavior."""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

from paddock import installer
from scripts import install, sync

REPO_ROOT = Path(__file__).resolve().parent.parent
FORBIDDEN_SKILL_PATTERNS = (
    "~/.claude",
    "superpowers:",
    "WebFetch",
    "WebSearch",
    "NotebookEdit",
)
CANONICAL_ROLES = ("builder", "generalist", "implementer", "planner", "ui", "verifier")
ANTIGRAVITY_AGENT_FILES = {
    "paddock-generalist.md",
    "paddock-implementer.md",
    "paddock-planner.md",
    "paddock-ui.md",
    "paddock-verifier.md",
}
CLAUDE_AGENT_FILES = {f"{role}.md" for role in CANONICAL_ROLES}


def _copy_tree(src: Path, dst: Path) -> None:
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))


def _make_sync_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    _copy_tree(REPO_ROOT / "core", repo / "core")
    for plat in ("antigravity", "claude"):
        (repo / "platforms" / plat / "scripts").mkdir(parents=True, exist_ok=True)
    return repo


def test_core_skills_have_valid_frontmatter_and_no_claude_leaks() -> None:
    skills_dir = REPO_ROOT / "core" / "skills"
    skill_dirs = [p for p in sorted(skills_dir.iterdir()) if p.is_dir()]
    assert {p.name for p in skill_dirs} == {
        "antipatterns",
        "memory",
        "paddock-models",
        "releasing",
        "verifying",
    }

    for sdir in skill_dirs:
        skill_md = sdir / "SKILL.md"
        assert skill_md.is_file()
        text = skill_md.read_text(encoding="utf-8")
        assert re.search(rf"(?m)^name:\s*{re.escape(sdir.name)}\s*$", text), sdir.name
        assert re.search(r"(?m)^description:\s*\S+", text), sdir.name
        for pat in FORBIDDEN_SKILL_PATTERNS:
            assert pat not in text, f"Forbidden Claude token {pat!r} in {skill_md}"


def test_sync_detects_drift_and_repairs_without_touching_orchestrating(tmp_path: Path) -> None:
    repo = _make_sync_repo(tmp_path)
    for plat in ("antigravity", "claude"):
        orch = repo / "platforms" / plat / "skills" / "orchestrating" / "SKILL.md"
        orch.parent.mkdir(parents=True, exist_ok=True)
        orch.write_text(f"---\nname: orchestrating\ndescription: {plat}\n---\nKeep.\n", encoding="utf-8")

    changed = sync.sync_all(repo, check=False)
    assert changed
    assert sync.sync_all(repo, check=True) == []

    # Mutate synced skill, paddock_core files, canonical agent copy, compiled agents, and add stale files.
    (repo / "platforms" / "antigravity" / "skills" / "verifying" / "SKILL.md").write_text(
        "stale content\n", encoding="utf-8"
    )
    (repo / "platforms" / "claude" / "scripts" / "paddock_core" / "memory.py").write_text(
        "# drifted\n", encoding="utf-8"
    )
    (repo / "platforms" / "antigravity" / "scripts" / "paddock_core" / "models.py").write_text(
        "# drifted models\n", encoding="utf-8"
    )
    (repo / "platforms" / "claude" / "scripts" / "paddock_core" / "agents.py").write_text(
        "# drifted agents\n", encoding="utf-8"
    )
    (repo / "platforms" / "claude" / "scripts" / "paddock_core" / "agents" / "implementer.md").write_text(
        "---\nname: implementer\n---\nDrifted.\n", encoding="utf-8"
    )
    (repo / "platforms" / "antigravity" / "agents" / "paddock-implementer.md").write_text(
        "# drifted agy agent\n", encoding="utf-8"
    )
    (repo / "platforms" / "claude" / "agents" / "implementer.md").write_text(
        "# drifted claude agent\n", encoding="utf-8"
    )

    extra_skill = repo / "platforms" / "antigravity" / "skills" / "verifying" / "stale.txt"
    extra_skill.write_text("leftover", encoding="utf-8")
    extra_core_agent = repo / "platforms" / "claude" / "scripts" / "paddock_core" / "agents" / "stale.md"
    extra_core_agent.write_text("leftover", encoding="utf-8")
    extra_agy_agent = repo / "platforms" / "antigravity" / "agents" / "paddock-builder.md"
    extra_agy_agent.write_text("should not exist", encoding="utf-8")
    extra_claude_agent = repo / "platforms" / "claude" / "agents" / "obsolete.md"
    extra_claude_agent.write_text("leftover", encoding="utf-8")

    drift = sync.sync_all(repo, check=True)
    assert len(drift) == 11

    sync.sync_all(repo, check=False)
    assert sync.sync_all(repo, check=True) == []
    for stale_path in (extra_skill, extra_core_agent, extra_agy_agent, extra_claude_agent):
        assert not stale_path.exists()
    assert "Keep." in (repo / "platforms" / "antigravity" / "skills" / "orchestrating" / "SKILL.md").read_text(
        encoding="utf-8"
    )


def test_sync_parity_compilation_and_ignores_custom_paddock_home_models(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _make_sync_repo(tmp_path)
    paddock_home = tmp_path / "custom_paddock"
    paddock_home.mkdir()
    (paddock_home / "models.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "preset": "quality",
                "overrides": {
                    "claude": {
                        "generalist": {"model": "opus", "effort": "high"},
                        "implementer": {"model": "opus", "effort": "high"},
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PADDOCK_HOME", str(paddock_home))

    sync.sync_all(repo, check=False)
    assert sync.sync_all(repo, check=True) == []

    # Verify config module and canonical agent copies inside paddock_core/ match core/ byte-for-byte.
    for plat in ("antigravity", "claude"):
        core_dst = repo / "platforms" / plat / "scripts" / "paddock_core"
        for cfg_name in ("models.py", "agents.py"):
            assert (core_dst / cfg_name).read_text(encoding="utf-8") == (
                repo / "core" / "config" / cfg_name
            ).read_text(encoding="utf-8")
        for role in CANONICAL_ROLES:
            assert (core_dst / "agents" / f"{role}.md").read_text(encoding="utf-8") == (
                repo / "core" / "agents" / f"{role}.md"
            ).read_text(encoding="utf-8")

    # Verify compiled platform agents use built-in balanced defaults, ignoring PADDOCK_HOME/models.json.
    agy_files = {p.name for p in (repo / "platforms" / "antigravity" / "agents").iterdir() if p.is_file()}
    claude_files = {p.name for p in (repo / "platforms" / "claude" / "agents").iterdir() if p.is_file()}
    assert agy_files == ANTIGRAVITY_AGENT_FILES
    assert claude_files == CLAUDE_AGENT_FILES

    claude_impl = (repo / "platforms" / "claude" / "agents" / "implementer.md").read_text(encoding="utf-8")
    claude_gen = (repo / "platforms" / "claude" / "agents" / "generalist.md").read_text(encoding="utf-8")
    agy_impl = (repo / "platforms" / "antigravity" / "agents" / "paddock-implementer.md").read_text(
        encoding="utf-8"
    )
    assert "\nmodel: sonnet\n" in claude_impl
    assert "\neffort: medium\n" in claude_impl
    assert "\nmodel: haiku\n" in claude_gen
    assert "\nmodel:" not in agy_impl

    # Updating a canonical core/agents/*.md file triggers drift across paddock_core/agents and compiled targets.
    core_impl = repo / "core" / "agents" / "implementer.md"
    core_impl.write_text(core_impl.read_text(encoding="utf-8") + "\n- Extra canonical rule.\n", encoding="utf-8")
    drift = sync.sync_all(repo, check=True)
    assert len(drift) == 4
    sync.sync_all(repo, check=False)
    assert sync.sync_all(repo, check=True) == []
    assert "Extra canonical rule." in (
        repo / "platforms" / "antigravity" / "agents" / "paddock-implementer.md"
    ).read_text(encoding="utf-8")
    assert "Extra canonical rule." in (repo / "platforms" / "claude" / "agents" / "implementer.md").read_text(
        encoding="utf-8"
    )


def test_install_antigravity_dry_run_and_install_preserves_config_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PADDOCK_HOME", raising=False)
    repo = tmp_path / "repo"
    _copy_tree(REPO_ROOT / "core", repo / "core")
    (repo / "platforms" / "antigravity").mkdir(parents=True)
    (repo / "platforms" / "antigravity" / "plugin.json").write_text('{"name":"paddock"}\n', encoding="utf-8")
    sync.sync_all(repo, check=False)

    fake_home = tmp_path / "home"
    cfg = fake_home / ".gemini" / "config" / "config.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text('{"sentinel": true}\n', encoding="utf-8")

    dry_msgs = install.install_antigravity(repo, fake_home, dry_run=True)
    assert any("[dry-run]" in m for m in dry_msgs)
    assert not (fake_home / ".gemini" / "config" / "plugins" / "paddock").exists()

    msgs = install.install_antigravity(repo, fake_home, dry_run=False)
    assert any("customization://paddock" in m for m in msgs)
    installed_plugin = fake_home / ".gemini" / "config" / "plugins" / "paddock"
    assert (installed_plugin / "plugin.json").is_file()
    assert (installed_plugin / "agents" / "paddock-implementer.md").is_file()
    assert (installed_plugin / "scripts" / "paddock_core" / "agents" / "implementer.md").is_file()
    assert cfg.read_text(encoding="utf-8") == '{"sentinel": true}\n'

    install.install_antigravity(repo, fake_home, uninstall=True)
    assert not installed_plugin.exists()


def test_install_antigravity_reinstall_preserves_locked_root_and_prunes_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PADDOCK_HOME", raising=False)
    repo = tmp_path / "repo"
    _copy_tree(REPO_ROOT / "core", repo / "core")
    (repo / "platforms" / "antigravity").mkdir(parents=True)
    (repo / "platforms" / "antigravity" / "plugin.json").write_text(
        '{"name":"paddock","version":"1.0.0"}\n', encoding="utf-8"
    )
    sync.sync_all(repo, check=False)

    fake_home = tmp_path / "home"
    cfg = fake_home / ".gemini" / "config" / "config.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text('{"sentinel": true}\n', encoding="utf-8")

    install.install_antigravity(repo, fake_home, dry_run=False)
    installed_plugin = fake_home / ".gemini" / "config" / "plugins" / "paddock"
    assert (installed_plugin / "plugin.json").is_file()

    stale_file = installed_plugin / "stale.txt"
    stale_file.write_text("stale\n", encoding="utf-8")
    stale_dir = installed_plugin / "stale_dir"
    stale_dir.mkdir()
    (stale_dir / "old.txt").write_text("old\n", encoding="utf-8")

    (repo / "platforms" / "antigravity" / "plugin.json").write_text(
        '{"name":"paddock","version":"2.0.0"}\n', encoding="utf-8"
    )
    orch_skill = repo / "platforms" / "antigravity" / "skills" / "orchestrating" / "SKILL.md"
    orch_skill.parent.mkdir(parents=True, exist_ok=True)
    orch_skill.write_text(
        "---\nname: orchestrating\ndescription: updated\n---\nUpdated.\n", encoding="utf-8"
    )

    rmtree_calls: list[Path] = []
    real_rmtree = shutil.rmtree

    def _spy_rmtree(path: str | os.PathLike[str], *args, **kwargs) -> None:
        rmtree_calls.append(Path(path))
        real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(install.shutil, "rmtree", _spy_rmtree)

    proc: subprocess.Popen[bytes] | None = None
    if os.name == "nt":
        proc = subprocess.Popen(
            [sys.executable, "-c", "import sys; sys.stdin.read()"],
            cwd=str(installed_plugin),
            stdin=subprocess.PIPE,
        )
    try:
        msgs = install.install_antigravity(repo, fake_home, dry_run=False)
    finally:
        if proc is not None:
            try:
                if proc.stdin is not None:
                    proc.stdin.close()
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)

    assert any("customization://paddock" in m for m in msgs)
    assert installed_plugin.resolve() not in {p.resolve() for p in rmtree_calls}
    assert installed_plugin.is_dir()
    assert (installed_plugin / "plugin.json").read_text(encoding="utf-8") == (
        '{"name":"paddock","version":"2.0.0"}\n'
    )
    assert "Updated." in (
        installed_plugin / "skills" / "orchestrating" / "SKILL.md"
    ).read_text(encoding="utf-8")
    assert (installed_plugin / "agents" / "paddock-implementer.md").is_file()
    assert not stale_file.exists()
    assert not stale_dir.exists()
    assert cfg.read_text(encoding="utf-8") == '{"sentinel": true}\n'


def test_install_claude_discovery_and_explicit_target_apply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PADDOCK_HOME", raising=False)
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    monkeypatch.setattr(installer.shutil, "which", lambda _name: None)
    monkeypatch.setattr(install.shutil, "which", lambda _name: None)
    repo = _make_sync_repo(tmp_path)
    (repo / ".claude-plugin").mkdir()
    (repo / ".claude-plugin" / "marketplace.json").write_text('{"name":"paddock"}\n', encoding="utf-8")
    sync.sync_all(repo, check=False)

    fake_home = tmp_path / "home"
    fake_home.mkdir()

    # Without an installed plugin target, return marketplace install guidance.
    no_target_msgs = install.install_claude(repo, fake_home)
    assert any("claude plugin install paddock@paddock" in m for m in no_target_msgs)

    # Configure global ~/.paddock/models.json and simulate an installed Claude Code plugin target.
    paddock_dir = fake_home / ".paddock"
    paddock_dir.mkdir()
    (paddock_dir / "models.json").write_text(
        json.dumps({"schema_version": 1, "preset": "quality"}),
        encoding="utf-8",
    )

    installed_plugin = fake_home / ".claude" / "plugins" / "cache" / "paddock" / "paddock" / "0.1.0"
    (installed_plugin / ".claude-plugin").mkdir(parents=True)
    (installed_plugin / ".claude-plugin" / "plugin.json").write_text('{"name":"paddock"}\n', encoding="utf-8")
    (installed_plugin / "agents").mkdir()
    (installed_plugin / "agents" / "implementer.md").write_text("old content\n", encoding="utf-8")
    (fake_home / ".claude" / "plugins" / "installed_plugins.json").write_text(
        json.dumps({"plugins": {"paddock@paddock": [{"installPath": str(installed_plugin)}]}}),
        encoding="utf-8",
    )

    dry_msgs = install.install_claude(repo, fake_home, dry_run=True)
    assert any("[dry-run]" in m for m in dry_msgs)
    assert (installed_plugin / "agents" / "implementer.md").read_text(encoding="utf-8") == "old content\n"

    apply_msgs = install.install_claude(repo, fake_home, dry_run=False)
    assert any("Applied Claude Code agents" in m for m in apply_msgs)
    assert "\nmodel: opus\n" in (installed_plugin / "agents" / "implementer.md").read_text(encoding="utf-8")
    assert "\neffort: high\n" in (installed_plugin / "agents" / "ui.md").read_text(encoding="utf-8")
    # Repo tracked platforms/claude/agents stays at balanced defaults.
    assert "\nmodel: sonnet\n" in (repo / "platforms" / "claude" / "agents" / "implementer.md").read_text(
        encoding="utf-8"
    )

    # Explicit --target + --home + --dry-run CLI invocation.
    explicit_target = tmp_path / "explicit-claude-plugin"
    explicit_target.mkdir()
    assert (
        install.main(
            [
                "--platform",
                "claude",
                "--repo",
                str(repo),
                "--home",
                str(fake_home),
                "--target",
                str(explicit_target),
                "--dry-run",
            ]
        )
        == 0
    )
    assert not (explicit_target / "agents").exists()

    assert (
        install.main(
            [
                "--platform",
                "claude",
                "--repo",
                str(repo),
                "--home",
                str(fake_home),
                "--target",
                str(explicit_target),
            ]
        )
        == 0
    )
    assert "\nmodel: opus\n" in (explicit_target / "agents" / "implementer.md").read_text(encoding="utf-8")

    # --target requires a single platform.
    assert (
        install.main(
            [
                "--platform",
                "all",
                "--repo",
                str(repo),
                "--home",
                str(fake_home),
                "--target",
                str(explicit_target),
            ]
        )
        == 1
    )


def test_repo_platforms_have_zero_sync_drift() -> None:
    for plat in ("antigravity", "claude"):
        plat_dir = REPO_ROOT / "platforms" / plat
        assert (plat_dir / "skills").is_dir()
        assert (plat_dir / "skills" / "paddock-models" / "SKILL.md").is_file()
        core_dir = plat_dir / "scripts" / "paddock_core"
        for mod_name in ("memory.py", "mcp_server.py", "models.py", "agents.py"):
            assert (core_dir / mod_name).is_file()
        for role in CANONICAL_ROLES:
            assert (core_dir / "agents" / f"{role}.md").is_file()

    assert {
        p.name for p in (REPO_ROOT / "platforms" / "antigravity" / "agents").iterdir() if p.is_file()
    } == ANTIGRAVITY_AGENT_FILES
    assert {
        p.name for p in (REPO_ROOT / "platforms" / "claude" / "agents").iterdir() if p.is_file()
    } == CLAUDE_AGENT_FILES
    assert sync.sync_all(REPO_ROOT, check=True) == []


def _make_version_parity_repo(tmp_path: Path) -> Path:
    repo = _make_sync_repo(tmp_path)
    (repo / "pyproject.toml").write_text(
        (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (repo / "paddock").mkdir(parents=True, exist_ok=True)
    (repo / "paddock" / "__init__.py").write_text(
        (REPO_ROOT / "paddock" / "__init__.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    agy_manifest = repo / "platforms" / "antigravity" / "plugin.json"
    agy_manifest.parent.mkdir(parents=True, exist_ok=True)
    agy_manifest.write_text(
        (REPO_ROOT / "platforms" / "antigravity" / "plugin.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    claude_manifest = repo / "platforms" / "claude" / ".claude-plugin" / "plugin.json"
    claude_manifest.parent.mkdir(parents=True, exist_ok=True)
    claude_manifest.write_text(
        (REPO_ROOT / "platforms" / "claude" / ".claude-plugin" / "plugin.json").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )
    sync.sync_all(repo, check=False)
    return repo


def test_check_version_parity_passes_on_repo_root() -> None:
    assert sync.check_version_parity(REPO_ROOT) == []


@pytest.mark.parametrize(
    "rel_path",
    [
        "pyproject.toml",
        "paddock/__init__.py",
        "platforms/antigravity/plugin.json",
        "platforms/claude/.claude-plugin/plugin.json",
        "core/memory/mcp_server.py",
        "platforms/antigravity/scripts/paddock_core/mcp_server.py",
        "platforms/claude/scripts/paddock_core/mcp_server.py",
    ],
)
def test_check_version_parity_catches_mismatch_in_all_seven_locations(
    tmp_path: Path, rel_path: str
) -> None:
    repo = _make_version_parity_repo(tmp_path)
    assert sync.check_version_parity(repo) == []

    target = repo / rel_path
    original = target.read_text(encoding="utf-8")
    if rel_path == "pyproject.toml":
        mutated = re.sub(r'(?m)^version\s*=\s*"[^"]+"', 'version = "9.9.9"', original, count=1)
    elif rel_path == "paddock/__init__.py":
        mutated = re.sub(r'(?m)^__version__\s*=\s*"[^"]+"', '__version__ = "9.9.9"', original, count=1)
    elif rel_path.endswith(".json"):
        data = json.loads(original)
        data["version"] = "9.9.9"
        mutated = json.dumps(data, indent=2) + "\n"
    else:
        mutated = re.sub(
            r'("version"\s*:\s*)"[^"]+"',
            r'\g<1>"9.9.9"',
            original,
            count=1,
        )
    assert mutated != original, rel_path
    target.write_text(mutated, encoding="utf-8")

    parity_errors = sync.check_version_parity(repo)
    assert parity_errors, f"Expected version drift for {rel_path}"
    assert any("version drift" in err and "9.9.9" in err for err in parity_errors)
    assert any(Path(rel_path).name in err for err in parity_errors)

    sync_errors = sync.sync_all(repo, check=True)
    assert any("version drift" in err for err in sync_errors)


def test_check_version_parity_reports_missing_unparseable_and_invalid_versions(
    tmp_path: Path,
) -> None:
    repo = _make_version_parity_repo(tmp_path)

    (repo / "platforms" / "claude" / ".claude-plugin" / "plugin.json").unlink()
    missing_errs = sync.check_version_parity(repo)
    assert any("version missing:" in err and "plugin.json" in err for err in missing_errs)

    (repo / "platforms" / "claude" / ".claude-plugin" / "plugin.json").write_text(
        "{broken json", encoding="utf-8"
    )
    unparseable_errs = sync.check_version_parity(repo)
    assert any("version unparseable:" in err and "plugin.json" in err for err in unparseable_errs)

    (repo / "platforms" / "claude" / ".claude-plugin" / "plugin.json").write_text(
        '{"name": "paddock", "version": "not-a-semver"}\n', encoding="utf-8"
    )
    invalid_errs = sync.check_version_parity(repo)
    assert any("version invalid:" in err and "not-a-semver" in err for err in invalid_errs)


def _parse_yaml_scalar(raw: str) -> Any:
    val = raw.strip()
    if val == "true":
        return True
    if val == "false":
        return False
    if val in ("null", "~"):
        return None
    if len(val) >= 2 and ((val[0] == '"' and val[-1] == '"') or (val[0] == "'" and val[-1] == "'")):
        return val[1:-1]
    if val.startswith("[") and val.endswith("]"):
        inner = val[1:-1].strip()
        if not inner:
            return []
        return [_parse_yaml_scalar(part) for part in inner.split(",")]
    return val


def _split_yaml_key_value(content: str) -> tuple[str, str | None]:
    if content.endswith(":"):
        return content[:-1].strip(), None
    if ": " in content:
        key, val = content.split(": ", 1)
        return key.strip(), val.strip()
    raise ValueError(f"Unsupported YAML line syntax: {content!r}")


def _parse_workflow_yaml(text: str) -> dict[str, Any]:
    """Minimal stdlib YAML-subset parser supporting workflow mappings, lists, and `|` blocks."""
    lines = text.splitlines()
    tokens: list[tuple[int, str, Any]] = []
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            idx += 1
            continue
        indent = len(line) - len(line.lstrip(" "))
        if stripped.startswith("- "):
            tokens.append((indent, "seq", stripped[2:]))
            idx += 1
            continue

        key, val = _split_yaml_key_value(stripped)
        if val == "|":
            idx += 1
            block_lines: list[str] = []
            while idx < len(lines):
                nxt = lines[idx]
                if not nxt.strip():
                    block_lines.append("")
                    idx += 1
                    continue
                nxt_indent = len(nxt) - len(nxt.lstrip(" "))
                if nxt_indent <= indent:
                    break
                block_lines.append(nxt)
                idx += 1
            while block_lines and not block_lines[-1].strip():
                block_lines.pop()
            parsed_val: Any = textwrap.dedent("\n".join(block_lines))
        else:
            parsed_val = None if val is None else _parse_yaml_scalar(val)
            idx += 1
        tokens.append((indent, "map", (key, parsed_val)))

    def parse_block(pos: int, base_indent: int) -> tuple[Any, int]:
        if pos >= len(tokens):
            return {}, pos
        _, kind, _ = tokens[pos]
        if kind == "seq":
            seq: list[Any] = []
            while pos < len(tokens):
                ind, k, payload = tokens[pos]
                if ind < base_indent or (ind == base_indent and k != "seq"):
                    break
                if ind != base_indent:
                    raise ValueError(f"Unexpected sequence indent {ind} != {base_indent}")
                item_str = str(payload).strip()
                pos += 1
                if ": " in item_str or item_str.endswith(":"):
                    item_key, item_raw = _split_yaml_key_value(item_str)
                    item_dict: dict[str, Any] = {}
                    item_indent = base_indent + 2
                    if item_raw == "|":
                        raise ValueError("Unsupported multiline block directly on sequence item")
                    if item_raw is None:
                        nested, pos = parse_block(pos, item_indent + 2)
                        item_dict[item_key] = nested
                    else:
                        item_dict[item_key] = _parse_yaml_scalar(item_raw)
                    if pos < len(tokens) and tokens[pos][0] >= item_indent and tokens[pos][1] == "map":
                        rest, pos = parse_block(pos, item_indent)
                        if not isinstance(rest, dict):
                            raise ValueError("Expected mapping continuation in sequence item")
                        item_dict.update(rest)
                    seq.append(item_dict)
                else:
                    seq.append(_parse_yaml_scalar(item_str))
            return seq, pos

        mapping: dict[str, Any] = {}
        while pos < len(tokens):
            ind, k, payload = tokens[pos]
            if ind < base_indent or k != "map":
                break
            if ind != base_indent:
                raise ValueError(f"Unexpected mapping indent {ind} != {base_indent}")
            key, val = payload
            pos += 1
            if val is None and pos < len(tokens) and tokens[pos][0] > base_indent:
                nested, pos = parse_block(pos, tokens[pos][0])
                mapping[key] = nested
            else:
                mapping[key] = val
        return mapping, pos

    parsed, next_pos = parse_block(0, 0)
    assert next_pos == len(tokens)
    assert isinstance(parsed, dict)
    return parsed


def test_github_workflows_structure_and_inline_scripts() -> None:
    ci_path = REPO_ROOT / ".github" / "workflows" / "ci.yml"
    release_path = REPO_ROOT / ".github" / "workflows" / "release.yml"
    assert ci_path.is_file()
    assert release_path.is_file()

    ci = _parse_workflow_yaml(ci_path.read_text(encoding="utf-8"))
    assert ci["name"] == "CI"
    assert ci["on"] == {
        "push": {"branches": ["main"]},
        "pull_request": {"branches": ["main"]},
    }
    assert ci["permissions"] == {"contents": "read"}
    ci_job = ci["jobs"]["test-and-package"]
    assert ci_job["runs-on"] == "${{ matrix.os }}"
    assert ci_job["strategy"]["fail-fast"] is False
    assert set(ci_job["strategy"]["matrix"]["os"]) == {"ubuntu-latest", "windows-latest"}

    ci_steps = ci_job["steps"]
    ci_uses = [step["uses"] for step in ci_steps if "uses" in step]
    assert "actions/checkout@v4" in ci_uses
    assert "actions/setup-python@v5" in ci_uses
    assert "astral-sh/setup-uv@v5" in ci_uses
    setup_py = next(s for s in ci_steps if s.get("uses") == "actions/setup-python@v5")
    assert setup_py["with"]["python-version"] == "3.12"

    ci_runs = [step["run"] for step in ci_steps if "run" in step]
    assert "python scripts/sync.py --check" in ci_runs
    assert "python -m pytest -q" in ci_runs
    assert "uv build --wheel --out-dir dist" in ci_runs

    for step in ci_steps:
        if step.get("shell") == "python":
            ast.parse(step["run"], filename=str(ci_path))

    wheel_check = next(s["run"] for s in ci_steps if "Verify wheel bundle" in s.get("name", ""))
    assert "paddock/_bundle/.claude-plugin/marketplace.json" in wheel_check
    assert "__pycache__" in wheel_check
    assert ".pytest_cache" in wheel_check

    smoke_step = next(s for s in ci_steps if "Smoke-test wheel" in s.get("name", ""))
    assert smoke_step["env"] == {
        "WORKSPACE_DIR": "${{ github.workspace }}",
        "TEMP_ROOT": "${{ runner.temp }}",
    }
    assert "source_mode" in smoke_step["run"]
    assert "bundle" in smoke_step["run"]
    assert "models" in smoke_step["run"]

    release = _parse_workflow_yaml(release_path.read_text(encoding="utf-8"))
    assert release["name"] == "Release"
    assert release["on"] == {"push": {"tags": ["v*"]}}
    assert release["permissions"] == {"contents": "write"}

    rel_job = release["jobs"]["release"]
    assert rel_job["runs-on"] == "ubuntu-latest"
    rel_steps = rel_job["steps"]
    rel_uses = [step["uses"] for step in rel_steps if "uses" in step]
    assert "actions/checkout@v4" in rel_uses
    assert "actions/setup-python@v5" in rel_uses
    assert "astral-sh/setup-uv@v5" in rel_uses

    rel_runs = [step["run"] for step in rel_steps if "run" in step]
    assert "python scripts/sync.py --check" in rel_runs
    assert "python -m pytest -q" in rel_runs
    assert "uv build --wheel --out-dir dist" in rel_runs

    for step in rel_steps:
        if step.get("shell") == "python":
            ast.parse(step["run"], filename=str(release_path))

    tag_step = next(s["run"] for s in rel_steps if "Validate release tag" in s.get("name", ""))
    assert "GITHUB_REF_NAME" in tag_step
    assert "pyproject.toml" in tag_step

    archive_step = next(
        s["run"] for s in rel_steps if "deterministic plugin archives" in s.get("name", "")
    )
    assert "paddock-antigravity-" in archive_step
    assert "paddock-claude-" in archive_step
    assert "SHA256SUMS.txt" in archive_step
    assert "(2025, 1, 1, 0, 0, 0)" in archive_step
    assert "__pycache__" in archive_step

    publish_step = next(s for s in rel_steps if "Publish or update GitHub Release" in s.get("name", ""))
    assert publish_step["env"] == {"GH_TOKEN": "${{ github.token }}"}
    assert "gh release view" in publish_step["run"]
    assert "gh release upload" in publish_step["run"]
    assert "--clobber" in publish_step["run"]
    assert "gh release create" in publish_step["run"]
    assert "--verify-tag --generate-notes" in publish_step["run"]
