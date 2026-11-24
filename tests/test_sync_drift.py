"""Tests for core skill/module sync parity, drift detection, and installer behavior."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from scripts import install, sync

REPO_ROOT = Path(__file__).resolve().parent.parent
FORBIDDEN_SKILL_PATTERNS = (
    "~/.claude",
    "superpowers:",
    "WebFetch",
    "WebSearch",
    "NotebookEdit",
)


def _copy_tree(src: Path, dst: Path) -> None:
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))


def test_core_skills_have_valid_frontmatter_and_no_claude_leaks() -> None:
    skills_dir = REPO_ROOT / "core" / "skills"
    skill_dirs = [p for p in sorted(skills_dir.iterdir()) if p.is_dir()]
    assert {p.name for p in skill_dirs} == {"antipatterns", "memory", "releasing", "verifying"}

    for sdir in skill_dirs:
        skill_md = sdir / "SKILL.md"
        assert skill_md.is_file()
        text = skill_md.read_text(encoding="utf-8")
        assert re.search(rf"(?m)^name:\s*{re.escape(sdir.name)}\s*$", text), sdir.name
        assert re.search(r"(?m)^description:\s*\S+", text), sdir.name
        for pat in FORBIDDEN_SKILL_PATTERNS:
            assert pat not in text, f"Forbidden Claude token {pat!r} in {skill_md}"


def test_sync_detects_drift_and_repairs_without_touching_orchestrating(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _copy_tree(REPO_ROOT / "core", repo / "core")
    for plat in ("antigravity", "claude"):
        orch = repo / "platforms" / plat / "skills" / "orchestrating" / "SKILL.md"
        orch.parent.mkdir(parents=True, exist_ok=True)
        orch.write_text(f"---\nname: orchestrating\ndescription: {plat}\n---\nKeep.\n", encoding="utf-8")
        (repo / "platforms" / plat / "scripts").mkdir(parents=True, exist_ok=True)

    changed = sync.sync_all(repo, check=False)
    assert changed
    assert sync.sync_all(repo, check=True) == []

    # Mutate one synced skill and one synced paddock_core file -> drift detected
    drifted_skill = repo / "platforms" / "antigravity" / "skills" / "verifying" / "SKILL.md"
    drifted_skill.write_text("stale content\n", encoding="utf-8")
    drifted_mem = repo / "platforms" / "claude" / "scripts" / "paddock_core" / "memory.py"
    drifted_mem.write_text("# drifted\n", encoding="utf-8")
    extra_file = repo / "platforms" / "antigravity" / "skills" / "verifying" / "stale.txt"
    extra_file.write_text("leftover", encoding="utf-8")

    drift = sync.sync_all(repo, check=True)
    assert len(drift) == 3

    sync.sync_all(repo, check=False)
    assert sync.sync_all(repo, check=True) == []
    assert not extra_file.exists()
    assert "Keep." in (repo / "platforms" / "antigravity" / "skills" / "orchestrating" / "SKILL.md").read_text(
        encoding="utf-8"
    )


def test_install_antigravity_dry_run_and_install_preserves_config_json(tmp_path: Path) -> None:
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
    assert (fake_home / ".gemini" / "config" / "plugins" / "paddock" / "plugin.json").is_file()
    assert cfg.read_text(encoding="utf-8") == '{"sentinel": true}\n'

    install.install_antigravity(repo, fake_home, uninstall=True)
    assert not (fake_home / ".gemini" / "config" / "plugins" / "paddock").exists()


def test_repo_platforms_have_zero_sync_drift() -> None:
    for plat in ("antigravity", "claude"):
        assert (REPO_ROOT / "platforms" / plat / "skills").is_dir()
        assert (REPO_ROOT / "platforms" / plat / "scripts" / "paddock_core" / "memory.py").is_file()
        assert (REPO_ROOT / "platforms" / plat / "scripts" / "paddock_core" / "mcp_server.py").is_file()
    assert sync.sync_all(REPO_ROOT, check=True) == []
