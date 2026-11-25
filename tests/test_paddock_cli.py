"""Tests for the unified `paddock` CLI, installer, bundle fallback, updater, and wrappers."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.error
from typing import Any

import pytest

import paddock
from paddock import cli, installer, updater, version
from scripts import install as install_script
from scripts import models as models_script

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _isolate_paddock_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tests isolated from host environment variables."""
    monkeypatch.delenv("PADDOCK_HOME", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)


def _copy_tree(src: Path, dst: Path) -> None:
    shutil.copytree(
        src,
        dst,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", ".pytest_cache"),
    )


def _build_bundle_tree(bundle_root: Path) -> Path:
    """Create a standalone wheel-style `_bundle/` tree outside any git repository."""
    _copy_tree(REPO_ROOT / "core", bundle_root / "core")
    _copy_tree(REPO_ROOT / "platforms" / "antigravity", bundle_root / "platforms" / "antigravity")
    _copy_tree(REPO_ROOT / "platforms" / "claude", bundle_root / "platforms" / "claude")
    marketplace_dst = bundle_root / ".claude-plugin" / "marketplace.json"
    marketplace_dst.parent.mkdir(parents=True, exist_ok=True)
    marketplace_dst.write_text(
        (REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return bundle_root


def _install_fake_claude_plugin(home_dir: Path, *, version: str = paddock.__version__) -> Path:
    """Create an installed Claude Code plugin cache entry and `installed_plugins.json`."""
    plugins_dir = home_dir / ".claude" / "plugins"
    installed_plugin = plugins_dir / "cache" / "paddock" / "paddock" / version
    (installed_plugin / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (installed_plugin / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "paddock", "version": version}) + "\n",
        encoding="utf-8",
    )
    (installed_plugin / "agents").mkdir(parents=True, exist_ok=True)
    (installed_plugin / "agents" / "implementer.md").write_text("placeholder\n", encoding="utf-8")
    (plugins_dir / "installed_plugins.json").write_text(
        json.dumps({"plugins": {"paddock@paddock": [{"installPath": str(installed_plugin)}]}}),
        encoding="utf-8",
    )
    return installed_plugin


def _completed(
    args: list[str],
    *,
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=args, returncode=returncode, stdout=stdout, stderr=stderr)


class _FakeHTTPResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._raw = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._raw

    def __enter__(self) -> _FakeHTTPResponse:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


def _patch_fake_bundle_module_location(
    monkeypatch: pytest.MonkeyPatch,
    site_root: Path,
) -> Path:
    """Point `installer.__file__` at a simulated installed package containing `_bundle/`."""
    pkg_dir = site_root / "paddock"
    pkg_dir.mkdir(parents=True, exist_ok=True)
    bundle_root = _build_bundle_tree(pkg_dir / "_bundle")
    monkeypatch.setattr(installer, "__file__", str(pkg_dir / "installer.py"))
    return bundle_root


def test_version_and_status_repo_and_bundle_modes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = cli.main(["--version"])
    assert rc == 0
    out = capsys.readouterr().out
    assert out.strip() == f"paddock {paddock.__version__}"

    fake_home = tmp_path / "home"
    work_dir = tmp_path / "work"
    fake_home.mkdir()
    work_dir.mkdir()

    # Repo mode status --json
    assert cli.main(["status", "--cwd", str(work_dir), "--home", str(fake_home), "--json"]) == 0
    repo_status = json.loads(capsys.readouterr().out)
    assert repo_status["version"] == paddock.__version__
    assert repo_status["source_mode"] == "repo"
    assert Path(repo_status["source_root"]).resolve() == REPO_ROOT.resolve()
    assert Path(repo_status["home_dir"]).resolve() == fake_home.resolve()
    assert repo_status["platforms"]["antigravity"]["installed"] is False
    assert repo_status["platforms"]["claude"]["installed"] is False
    assert set(repo_status["tools"]) == {"uv", "claude", "git", "gh"}
    assert "preset" in repo_status["models"]

    # Installed plugin + global models.json show up in human-readable text output too
    assert installer.source_mode(REPO_ROOT) == "repo"
    (fake_home / ".paddock").mkdir()
    (fake_home / ".paddock" / "models.json").write_text(
        json.dumps({"schema_version": 1, "preset": "quality"}),
        encoding="utf-8",
    )
    (fake_home / ".gemini" / "config" / "plugins" / "paddock").mkdir(parents=True)
    (fake_home / ".gemini" / "config" / "plugins" / "paddock" / "plugin.json").write_text(
        json.dumps({"name": "paddock", "version": paddock.__version__}),
        encoding="utf-8",
    )
    _install_fake_claude_plugin(fake_home)

    assert cli.main(["status", "--cwd", str(work_dir), "--home", str(fake_home)]) == 0
    text_status = capsys.readouterr().out
    assert f"paddock {paddock.__version__} (source: repo" in text_status
    assert f"installed (v{paddock.__version__})" in text_status
    assert "preset:  quality" in text_status

    # Real _bundle/ mode resolution via simulated site-packages layout
    bundle_root = _patch_fake_bundle_module_location(monkeypatch, tmp_path / "site-packages")
    assert installer.source_mode(bundle_root) == "bundle"
    assert cli.main(["status", "--cwd", str(work_dir), "--home", str(fake_home), "--json"]) == 0
    bundle_status = json.loads(capsys.readouterr().out)
    assert bundle_status["version"] == paddock.__version__
    assert bundle_status["source_mode"] == "bundle"
    assert Path(bundle_status["source_root"]).resolve() == bundle_root.resolve()


def test_install_auto_detection_matrix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # 1. Neither detected -> guidance message and exit 0
    empty_home = tmp_path / "empty_home"
    empty_home.mkdir()
    monkeypatch.setattr(installer.shutil, "which", lambda _name: None)

    assert cli.main(["install", "--home", str(empty_home)]) == 0
    out_none = capsys.readouterr().out
    assert "No supported platforms detected" in out_none
    assert "paddock install antigravity" in out_none
    assert "paddock install claude" in out_none

    assert cli.main(["install", "--home", str(empty_home), "--json"]) == 0
    json_none = json.loads(capsys.readouterr().out)
    assert json_none["detected"] is False
    assert json_none["auto_detected"] is True
    assert json_none["platforms"] == []

    # 2. Antigravity-only
    agy_home = tmp_path / "agy_home"
    (agy_home / ".gemini").mkdir(parents=True)
    assert cli.main(["install", "--home", str(agy_home), "--json"]) == 0
    json_agy = json.loads(capsys.readouterr().out)
    assert json_agy["detected"] is True
    assert json_agy["auto_detected"] is True
    assert json_agy["platforms"] == ["antigravity"]
    assert (agy_home / ".gemini" / "config" / "plugins" / "paddock" / "plugin.json").is_file()
    assert (
        agy_home / ".gemini" / "config" / "plugins" / "paddock" / "agents" / "paddock-implementer.md"
    ).is_file()
    assert not (agy_home / ".claude").exists()

    # 3. Claude-only (detected via claude CLI on PATH, without ~/.claude directory)
    claude_path_home = tmp_path / "claude_path_home"
    claude_path_home.mkdir()
    monkeypatch.setattr(
        installer.shutil,
        "which",
        lambda name: "/usr/bin/claude" if name == "claude" else None,
    )
    assert installer.detect_platforms(claude_path_home) == ["claude"]

    calls: list[list[str]] = []

    def _fake_claude_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(list(args))
        assert kwargs.get("shell") is False
        return _completed(args, returncode=0)

    monkeypatch.setattr(installer.subprocess, "run", _fake_claude_run)
    assert cli.main(["install", "--home", str(claude_path_home), "--json"]) == 0
    json_claude = json.loads(capsys.readouterr().out)
    assert json_claude["platforms"] == ["claude"]
    assert json_claude["auto_detected"] is True
    assert not (claude_path_home / ".gemini").exists()
    assert calls == [
        ["/usr/bin/claude", "plugin", "marketplace", "add", "vdtongdev/paddock"],
        ["/usr/bin/claude", "plugin", "install", "paddock@paddock"],
    ]

    # 4. Both detected
    calls.clear()
    both_home = tmp_path / "both_home"
    (both_home / ".gemini").mkdir(parents=True)
    (both_home / ".claude").mkdir(parents=True)
    assert cli.main(["install", "--home", str(both_home), "--json"]) == 0
    json_both = json.loads(capsys.readouterr().out)
    assert json_both["platforms"] == ["antigravity", "claude"]
    assert json_both["auto_detected"] is True
    assert (both_home / ".gemini" / "config" / "plugins" / "paddock" / "plugin.json").is_file()
    assert calls == [
        ["/usr/bin/claude", "plugin", "marketplace", "add", "vdtongdev/paddock"],
        ["/usr/bin/claude", "plugin", "install", "paddock@paddock"],
    ]

    # Conflicting positional vs --platform flag is rejected
    assert cli.main(["install", "antigravity", "--platform", "claude", "--home", str(both_home)]) == 1
    assert "Conflicting platform arguments" in capsys.readouterr().err


def test_install_claude_marketplace_execution_and_fallbacks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake_home = tmp_path / "home"
    (fake_home / ".paddock").mkdir(parents=True)
    (fake_home / ".paddock" / "models.json").write_text(
        json.dumps({"schema_version": 1, "preset": "quality"}),
        encoding="utf-8",
    )

    claude_bin = "/mock/bin/claude"
    monkeypatch.setattr(
        installer.shutil,
        "which",
        lambda name: claude_bin if name == "claude" else None,
    )

    recorded: list[tuple[list[str], dict[str, Any]]] = []
    installed_plugin_holder: dict[str, Path] = {}

    def _run_fresh(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        recorded.append((list(args), dict(kwargs)))
        if args[1:4] == ["plugin", "install", "paddock@paddock"]:
            installed_plugin_holder["path"] = _install_fake_claude_plugin(fake_home)
        return _completed(args, returncode=0)

    monkeypatch.setattr(installer.subprocess, "run", _run_fresh)
    assert cli.main(["install", "claude", "--home", str(fake_home)]) == 0
    out = capsys.readouterr().out
    assert "Registered Claude Code marketplace (vdtongdev/paddock)" in out
    assert "Installed Claude Code plugin (paddock@paddock)" in out
    assert "Applied Claude Code agents" in out

    assert [cmd for cmd, _ in recorded] == [
        [claude_bin, "plugin", "marketplace", "add", "vdtongdev/paddock"],
        [claude_bin, "plugin", "install", "paddock@paddock"],
    ]
    expected_home = str(fake_home.resolve())
    for _, kwargs in recorded:
        assert kwargs["shell"] is False
        assert kwargs["env"]["HOME"] == expected_home
        assert kwargs["env"]["USERPROFILE"] == expected_home

    installed_impl = (installed_plugin_holder["path"] / "agents" / "implementer.md").read_text(
        encoding="utf-8"
    )
    installed_ui = (installed_plugin_holder["path"] / "agents" / "ui.md").read_text(
        encoding="utf-8"
    )
    assert "\nmodel: opus\n" in installed_impl
    assert "\neffort: medium\n" in installed_impl
    assert "\neffort: high\n" in installed_ui

    # Fallback to `marketplace update paddock` and `plugin update paddock@paddock` when already registered
    fresh_home2 = tmp_path / "home2"
    fresh_home2.mkdir()
    recorded.clear()

    def _run_already_registered(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        recorded.append((list(args), dict(kwargs)))
        if args[1:4] == ["plugin", "marketplace", "add"]:
            return _completed(args, returncode=1, stderr="Marketplace 'paddock' already exists")
        if args[1:4] == ["plugin", "install", "paddock@paddock"]:
            return _completed(args, returncode=1, stdout="Plugin paddock@paddock is already installed")
        return _completed(args, returncode=0)

    monkeypatch.setattr(installer.subprocess, "run", _run_already_registered)
    assert cli.main(["install", "claude", "--home", str(fresh_home2)]) == 0
    out_upd = capsys.readouterr().out
    assert "Updated Claude Code marketplace (paddock)" in out_upd
    assert "Updated Claude Code plugin (paddock@paddock)" in out_upd
    assert [cmd for cmd, _ in recorded] == [
        [claude_bin, "plugin", "marketplace", "add", "vdtongdev/paddock"],
        [claude_bin, "plugin", "marketplace", "update", "paddock"],
        [claude_bin, "plugin", "install", "paddock@paddock"],
        [claude_bin, "plugin", "update", "paddock@paddock"],
    ]
    expected_home2 = str(fresh_home2.resolve())
    for _, kwargs in recorded:
        assert kwargs["shell"] is False
        assert kwargs["env"]["HOME"] == expected_home2
        assert kwargs["env"]["USERPROFILE"] == expected_home2

    # --dry-run never invokes subprocess.run even when `claude` is on PATH
    def _forbid_subprocess(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise AssertionError(f"subprocess.run must not be invoked here: {args!r}")

    monkeypatch.setattr(installer.subprocess, "run", _forbid_subprocess)
    assert cli.main(["install", "claude", "--home", str(fresh_home2), "--dry-run"]) == 0
    dry_out = capsys.readouterr().out
    assert "[dry-run] run: claude plugin marketplace add vdtongdev/paddock" in dry_out
    assert "[dry-run] run: claude plugin install paddock@paddock" in dry_out

    # Missing `claude` CLI prints copy-paste commands without invoking subprocess.run
    monkeypatch.setattr(installer.shutil, "which", lambda _name: None)
    assert cli.main(["install", "claude", "--home", str(fresh_home2)]) == 0
    missing_out = capsys.readouterr().out
    assert "claude plugin marketplace add vdtongdev/paddock" in missing_out
    assert "claude plugin install paddock@paddock" in missing_out


def test_uninstall_both_platforms_dry_run_and_real_preserves_paddock_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake_home = tmp_path / "home"
    agy_plugin = fake_home / ".gemini" / "config" / "plugins" / "paddock"
    agy_plugin.mkdir(parents=True)
    (agy_plugin / "plugin.json").write_text('{"name":"paddock"}\n', encoding="utf-8")

    cfg_json = fake_home / ".gemini" / "config" / "config.json"
    cfg_json.write_text('{"sentinel": true}\n', encoding="utf-8")

    (fake_home / ".claude").mkdir(parents=True)

    paddock_dir = fake_home / ".paddock"
    mem_file = paddock_dir / "memory" / "global" / "keep.md"
    mem_file.parent.mkdir(parents=True)
    mem_file.write_text("---\nid: keep\n---\nImportant user memory.\n", encoding="utf-8")
    models_file = paddock_dir / "models.json"
    models_file.write_text('{"schema_version": 1, "preset": "quality"}\n', encoding="utf-8")

    claude_bin = "/mock/bin/claude"
    monkeypatch.setattr(
        installer.shutil,
        "which",
        lambda name: claude_bin if name == "claude" else None,
    )

    def _forbid_subprocess(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise AssertionError(f"subprocess.run must not run on --dry-run: {args!r}")

    monkeypatch.setattr(installer.subprocess, "run", _forbid_subprocess)
    assert cli.main(["uninstall", "all", "--home", str(fake_home), "--dry-run"]) == 0
    dry_out = capsys.readouterr().out
    assert "[dry-run] remove" in dry_out
    assert "claude plugin uninstall paddock@paddock" in dry_out
    assert agy_plugin.is_dir()

    uninstall_calls: list[tuple[list[str], dict[str, Any]]] = []

    def _run_uninstall(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        uninstall_calls.append((list(args), dict(kwargs)))
        return _completed(args, returncode=0)

    monkeypatch.setattr(installer.subprocess, "run", _run_uninstall)
    assert cli.main(["uninstall", "all", "--home", str(fake_home)]) == 0
    real_out = capsys.readouterr().out
    assert f"Removed {agy_plugin.resolve()}" in real_out
    assert "Uninstalled Claude Code plugin paddock@paddock" in real_out
    assert not agy_plugin.exists()
    assert uninstall_calls == [([claude_bin, "plugin", "uninstall", "paddock@paddock"], uninstall_calls[0][1])]
    assert uninstall_calls[0][1]["shell"] is False
    assert uninstall_calls[0][1]["env"]["HOME"] == str(fake_home.resolve())
    assert uninstall_calls[0][1]["env"]["USERPROFILE"] == str(fake_home.resolve())

    # ~/.paddock/memory, ~/.paddock/models.json, and ~/.gemini/config/config.json remain intact
    assert mem_file.read_text(encoding="utf-8") == "---\nid: keep\n---\nImportant user memory.\n"
    assert models_file.read_text(encoding="utf-8") == '{"schema_version": 1, "preset": "quality"}\n'
    assert cfg_json.read_text(encoding="utf-8") == '{"sentinel": true}\n'

    # Missing claude CLI on uninstall prints fallback command instead of failing
    monkeypatch.setattr(installer.shutil, "which", lambda _name: None)
    assert cli.main(["uninstall", "claude", "--home", str(fake_home)]) == 0
    assert "claude plugin uninstall paddock@paddock" in capsys.readouterr().out


def test_models_delegation_and_memory_absent_from_public_cli(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    paddock_home = tmp_path / "paddock_home"
    monkeypatch.setenv("PADDOCK_HOME", str(paddock_home))

    assert cli.main(["models", "show", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["effective_preset"] == "balanced"
    assert set(payload["platforms"]) >= {"antigravity", "claude"}

    assert cli.main(["models", "--help"]) == 0
    models_help = capsys.readouterr().out
    assert "show" in models_help

    # `paddock memory` must fail and never appear among public CLI commands
    parser = cli._build_parser()
    sub_actions = [
        action for action in parser._actions if hasattr(action, "choices") and isinstance(action.choices, dict)
    ]
    assert len(sub_actions) == 1
    assert set(sub_actions[0].choices) == {"install", "uninstall", "update", "status", "models"}
    assert "memory" not in sub_actions[0].choices

    assert cli.main(["memory"]) != 0
    err = capsys.readouterr().err
    assert "invalid choice: 'memory'" in err

    assert cli.main(["--help"]) == 0
    top_help = capsys.readouterr().out
    assert "{install,uninstall,update,status,models}" in top_help


def test_wheel_bundle_fallback_and_verify_bundle_integrity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    site_root = tmp_path / "site-packages"
    bundle_root = _patch_fake_bundle_module_location(monkeypatch, site_root)
    assert not (bundle_root / "scripts" / "sync.py").exists()
    assert not (site_root / "scripts" / "sync.py").exists()

    # Valid bundle passes verify_bundle_integrity
    installer.verify_bundle_integrity(bundle_root)
    resolved_root, mode = installer.resolve_source_root()
    assert resolved_root == bundle_root.resolve()
    assert mode == "bundle"

    # Installing from _bundle/ must never import or invoke scripts/sync.py
    def _forbid_sync(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("scripts/sync.py must not be loaded in _bundle/ mode")

    monkeypatch.setattr(installer, "_load_sync_module", _forbid_sync)

    fake_home = tmp_path / "home"
    (fake_home / ".gemini").mkdir(parents=True)
    msgs = installer.install_antigravity(bundle_root, fake_home, dry_run=False)
    assert any("Installed Antigravity plugin" in m for m in msgs)
    installed_plugin = fake_home / ".gemini" / "config" / "plugins" / "paddock"
    assert (installed_plugin / "plugin.json").is_file()
    assert (installed_plugin / "agents" / "paddock-implementer.md").is_file()

    # install_targets auto-resolves _bundle/ outside any repo
    res = installer.install_targets(platform="antigravity", home_dir=fake_home)
    assert res["source_mode"] == "bundle"
    assert Path(res["source_root"]).resolve() == bundle_root.resolve()

    # verify_bundle_integrity catches missing required bundle files
    for rel_missing in (
        "platforms/antigravity/plugin.json",
        "platforms/antigravity/scripts/paddock_core/mcp_server.py",
        "platforms/claude/.claude-plugin/plugin.json",
        ".claude-plugin/marketplace.json",
        "platforms/claude/scripts/paddock_core/agents.py",
        "core/skills/paddock-models/SKILL.md",
    ):
        broken_bundle = _build_bundle_tree(tmp_path / f"broken_{rel_missing.replace('/', '_')}")
        (broken_bundle / rel_missing).unlink()
        with pytest.raises(FileNotFoundError, match="Incomplete Paddock bundle"):
            installer.verify_bundle_integrity(broken_bundle)

    # Missing core/agents/*.md is also caught
    no_agents_bundle = _build_bundle_tree(tmp_path / "broken_no_agents")
    for md in (no_agents_bundle / "core" / "agents").glob("*.md"):
        md.unlink()
    with pytest.raises(FileNotFoundError, match=r"core/agents/\*\.md"):
        installer.verify_bundle_integrity(no_agents_bundle)

    # install_antigravity checks required assets when scripts/sync.py is absent
    broken_agy = _build_bundle_tree(tmp_path / "broken_agy")
    (broken_agy / "platforms" / "antigravity" / "scripts" / "paddock_core" / "models.py").unlink()
    with pytest.raises(FileNotFoundError, match="Incomplete Paddock bundle"):
        installer.install_antigravity(broken_agy, fake_home, dry_run=False)

    # install_targets in bundle mode runs verify_bundle_integrity before installing
    broken_bundle_site = tmp_path / "broken_site"
    broken_active = _patch_fake_bundle_module_location(monkeypatch, broken_bundle_site)
    (broken_active / "platforms" / "claude" / "scripts" / "paddock_core" / "models.py").unlink()
    with pytest.raises(FileNotFoundError, match="Incomplete Paddock bundle"):
        installer.install_targets(platform="antigravity", home_dir=fake_home)


def test_update_check_upgrade_and_gh_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    captured_requests: list[Any] = []
    (major, minor, _patch), _ = version.parse_semver(paddock.__version__)
    newer_tag = f"v{major}.{minor + 1}.0"
    fallback_tag = f"v{major}.{minor + 2}.0"

    def _fake_urlopen(req: Any, timeout: float = 15.0) -> _FakeHTTPResponse:
        captured_requests.append(req)
        return _FakeHTTPResponse({"tag_name": newer_tag})

    monkeypatch.setattr(updater.urllib.request, "urlopen", _fake_urlopen)

    # --check with newer tag (no token)
    assert cli.main(["update", "--check", "--json"]) == 0
    check_data = json.loads(capsys.readouterr().out)
    assert check_data["status"] == "update-available"
    assert check_data["target_tag"] == newer_tag
    assert check_data["ref_source"] == "github-api"
    assert check_data["update_available"] is True
    assert captured_requests[0].full_url == updater.GITHUB_LATEST_RELEASE_API
    assert captured_requests[0].get_header("Authorization") is None

    # --check sends Bearer header when GITHUB_TOKEN is set
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_checkHeaderToken123")
    monkeypatch.setattr(
        updater.urllib.request,
        "urlopen",
        lambda req, timeout=15.0: (
            captured_requests.append(req)
            or _FakeHTTPResponse({"tag_name": f"v{paddock.__version__}"})
        ),
    )
    assert cli.main(["update", "--check", "--json"]) == 0
    up_to_date = json.loads(capsys.readouterr().out)
    assert up_to_date["status"] == "up-to-date"
    assert up_to_date["update_available"] is False
    assert captured_requests[-1].get_header("Authorization") == "Bearer ghp_checkHeaderToken123"
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    # Full upgrade via mocked urllib + uv + upgraded paddock binary
    monkeypatch.setattr(updater.urllib.request, "urlopen", _fake_urlopen)
    monkeypatch.setattr(
        updater.shutil,
        "which",
        lambda name: f"/mock/bin/{name}" if name in ("uv", "paddock") else None,
    )
    upgrade_calls: list[tuple[list[str], dict[str, Any]]] = []

    def _run_upgrade(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        upgrade_calls.append((list(args), dict(kwargs)))
        if Path(args[0]).name == "paddock":
            return _completed(args, returncode=0, stdout=json.dumps({"action": "install", "platforms": ["antigravity"]}))
        return _completed(args, returncode=0)

    monkeypatch.setattr(updater.subprocess, "run", _run_upgrade)
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    assert cli.main(["update", "--home", str(fake_home), "--json"]) == 0
    upgrade_data = json.loads(capsys.readouterr().out)
    assert upgrade_data["status"] == "updated"
    assert upgrade_data["target_ref"] == newer_tag
    assert upgrade_data["plugin_install"] == {"action": "install", "platforms": ["antigravity"]}
    assert [cmd for cmd, _ in upgrade_calls] == [
        [
            "/mock/bin/uv",
            "tool",
            "install",
            "--force",
            f"git+https://github.com/vdtongdev/paddock.git@{newer_tag}",
        ],
        [
            "/mock/bin/paddock",
            "install",
            "--update",
            "--home",
            str(fake_home.resolve()),
            "--json",
        ],
    ]
    assert all(kwargs["shell"] is False for _, kwargs in upgrade_calls)

    # Fallback to `gh api` when urllib fails
    def _fail_urlopen(req: Any, timeout: float = 15.0) -> Any:
        raise urllib.error.URLError("rate limit exceeded")

    gh_calls: list[tuple[list[str], dict[str, Any]]] = []

    def _run_gh(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        gh_calls.append((list(args), dict(kwargs)))
        return _completed(args, returncode=0, stdout=json.dumps({"tag_name": fallback_tag}))

    monkeypatch.setattr(updater.urllib.request, "urlopen", _fail_urlopen)
    monkeypatch.setattr(
        updater.shutil,
        "which",
        lambda name: "/mock/bin/gh" if name == "gh" else None,
    )
    monkeypatch.setattr(updater.subprocess, "run", _run_gh)
    assert cli.main(["update", "--check", "--json"]) == 0
    gh_data = json.loads(capsys.readouterr().out)
    assert gh_data["target_tag"] == fallback_tag
    assert gh_data["ref_source"] == "gh-cli"
    assert gh_calls[0][0] == ["/mock/bin/gh", "api", "repos/vdtongdev/paddock/releases/latest"]
    assert gh_calls[0][1]["shell"] is False


def test_update_offline_error_sanitizes_tokens(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret_a = "ghp_superSecretTokenValue123456"
    secret_b = "github_pat_anotherSecretValue7890"
    monkeypatch.setenv("GITHUB_TOKEN", secret_a)
    monkeypatch.setenv("GH_TOKEN", secret_b)

    def _fail_with_secret(req: Any, timeout: float = 15.0) -> Any:
        raise urllib.error.URLError(
            f"Authorization: Bearer {secret_a} failed with {secret_b}"
        )

    monkeypatch.setattr(updater.urllib.request, "urlopen", _fail_with_secret)
    monkeypatch.setattr(updater.shutil, "which", lambda _name: None)

    assert cli.main(["update", "--check"]) == 1
    cap1 = capsys.readouterr()
    assert secret_a not in cap1.out and secret_a not in cap1.err
    assert secret_b not in cap1.out and secret_b not in cap1.err
    assert "[REDACTED]" in cap1.err

    # Also test when `gh` fallback runs and echoes secrets
    monkeypatch.setattr(
        updater.shutil,
        "which",
        lambda name: "/mock/bin/gh" if name == "gh" else None,
    )
    monkeypatch.setattr(
        updater.subprocess,
        "run",
        lambda args, **kwargs: _completed(
            args,
            returncode=1,
            stderr=f"Bearer {secret_b} / {secret_a} denied",
        ),
    )
    assert cli.main(["update", "--check"]) == 1
    cap2 = capsys.readouterr()
    assert secret_a not in cap2.out and secret_a not in cap2.err
    assert secret_b not in cap2.out and secret_b not in cap2.err
    assert "[REDACTED]" in cap2.err


@pytest.mark.parametrize(
    "bad_tag",
    [
        "0.2.0",
        "v1.2",
        "bad;tag",
        "v0.2.0 && calc",
        "v0.2.0\nmain",
        "--config=evil",
        "v1.2.3-",
        "v1.2.3-a..b",
    ],
)
def test_update_rejects_invalid_version_tags(
    bad_tag: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        updater.urllib.request,
        "urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("urlopen must not run")),
    )
    monkeypatch.setattr(
        updater.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("subprocess.run must not run")),
    )

    assert cli.main(["update", f"--version={bad_tag}", "--dry-run"]) == 1
    err = capsys.readouterr().err
    assert "Invalid release tag" in err or "Invalid empty prerelease identifier" in err


def test_update_main_dry_run_and_plugins_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake_home = tmp_path / "home"
    (fake_home / ".gemini").mkdir(parents=True)

    monkeypatch.setattr(
        updater.urllib.request,
        "urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("urlopen must not run")),
    )
    monkeypatch.setattr(
        updater.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("subprocess.run must not run")),
    )
    monkeypatch.setattr(installer.shutil, "which", lambda _name: None)

    # --main and --version are mutually exclusive
    assert cli.main(["update", "--main", "--version", "v0.2.0", "--dry-run"]) == 1
    assert "Cannot combine --version and --main" in capsys.readouterr().err

    # --main --dry-run
    assert cli.main(["update", "--main", "--dry-run", "--home", str(fake_home), "--json"]) == 0
    main_dry = json.loads(capsys.readouterr().out)
    assert main_dry["mode"] == "dry-run"
    assert main_dry["target_ref"] == "main"
    assert main_dry["ref_source"] == "main"
    assert any(
        "[dry-run] run: uv tool install --force git+https://github.com/vdtongdev/paddock.git@main" in m
        for m in main_dry["messages"]
    )
    assert not (fake_home / ".gemini" / "config" / "plugins" / "paddock").exists()

    # Valid prerelease --version --dry-run
    assert (
        cli.main(["update", "--version", "v1.2.3-rc.1", "--dry-run", "--home", str(fake_home), "--json"])
        == 0
    )
    ver_dry = json.loads(capsys.readouterr().out)
    assert ver_dry["target_ref"] == "v1.2.3-rc.1"

    # --plugins-only syncs plugins in-process without invoking uv or GitHub API
    monkeypatch.setattr(
        updater.shutil,
        "which",
        lambda name: (_ for _ in ()).throw(AssertionError("uv must not be looked up"))
        if name == "uv"
        else None,
    )
    assert cli.main(["update", "--plugins-only", "--home", str(fake_home), "--json"]) == 0
    plugins_only_data = json.loads(capsys.readouterr().out)
    assert plugins_only_data["mode"] == "plugins-only"
    assert plugins_only_data["status"] == "updated"
    assert (fake_home / ".gemini" / "config" / "plugins" / "paddock" / "plugin.json").is_file()


@pytest.mark.parametrize(
    "lock_stderr",
    [
        "error: failed to replace file: Access is denied. (os error 5) [WinError 5]",
        "The process cannot access the file because it is being used by another process. (WinError 32)",
    ],
)
def test_update_windows_self_update_lock_leaves_plugins_untouched(
    lock_stderr: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake_home = tmp_path / "home"
    (fake_home / ".gemini").mkdir(parents=True)
    agy_plugin = fake_home / ".gemini" / "config" / "plugins" / "paddock"
    agy_plugin.mkdir(parents=True)
    sentinel_manifest = '{"name":"paddock","version":"0.1.0","sentinel":true}\n'
    (agy_plugin / "plugin.json").write_text(sentinel_manifest, encoding="utf-8")

    monkeypatch.setattr(
        updater.shutil,
        "which",
        lambda name: "C:\\uv\\uv.exe" if name == "uv" else None,
    )
    uv_calls: list[list[str]] = []

    def _run_locked_uv(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        uv_calls.append(list(args))
        return _completed(args, returncode=1, stderr=lock_stderr)

    monkeypatch.setattr(updater.subprocess, "run", _run_locked_uv)

    assert cli.main(["update", "--version", "v0.3.0", "--home", str(fake_home)]) == 1
    cap = capsys.readouterr()
    assert len(uv_calls) == 1
    err_lines = [line.strip() for line in cap.err.splitlines() if line.strip()]
    expected_line_1 = "uv tool install --force git+https://github.com/vdtongdev/paddock.git@v0.3.0"
    expected_line_2 = "paddock install"
    assert expected_line_1 in err_lines
    assert expected_line_2 in err_lines
    assert err_lines.index(expected_line_2) == err_lines.index(expected_line_1) + 1
    assert (agy_plugin / "plugin.json").read_text(encoding="utf-8") == sentinel_manifest

    # Also verify --json locked payload
    assert cli.main(["update", "--version", "v0.3.0", "--home", str(fake_home), "--json"]) == 1
    locked_json = json.loads(capsys.readouterr().out)
    assert locked_json["status"] == "locked"
    assert locked_json["exit_code"] == 1
    assert locked_json["recovery_commands"] == [expected_line_1, expected_line_2]


def test_backward_compatible_script_wrappers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    for attr in ("install_antigravity", "install_claude", "main", "shutil", "_copy_plugin_tree"):
        assert hasattr(install_script, attr)

    fake_home = tmp_path / "home"
    (fake_home / ".gemini").mkdir(parents=True)
    monkeypatch.setattr(installer.shutil, "which", lambda _name: None)

    assert install_script.main(["--platform", "antigravity", "--home", str(fake_home), "--dry-run"]) == 0
    assert "[dry-run]" in capsys.readouterr().out
    assert not (fake_home / ".gemini" / "config" / "plugins" / "paddock").exists()

    assert install_script.main(["--platform", "antigravity", "--home", str(fake_home)]) == 0
    assert "Installed Antigravity plugin" in capsys.readouterr().out
    assert (fake_home / ".gemini" / "config" / "plugins" / "paddock" / "plugin.json").is_file()

    assert install_script.main(["--platform", "antigravity", "--home", str(fake_home), "--uninstall"]) == 0
    assert "Removed" in capsys.readouterr().out
    assert not (fake_home / ".gemini" / "config" / "plugins" / "paddock").exists()

    # models.py wrapper matches `paddock models show --json`
    paddock_home = tmp_path / "paddock_home"
    monkeypatch.setenv("PADDOCK_HOME", str(paddock_home))
    assert models_script.main(["show", "--json"]) == 0
    wrapper_models = json.loads(capsys.readouterr().out)
    assert cli.main(["models", "show", "--json"]) == 0
    cli_models = json.loads(capsys.readouterr().out)
    assert wrapper_models == cli_models

    # End-to-end subprocess checks in isolated environment
    env = os.environ.copy()
    env["HOME"] = str(fake_home)
    env["USERPROFILE"] = str(fake_home)
    env["PADDOCK_HOME"] = str(paddock_home)

    install_proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "install.py"), "--platform", "all", "--dry-run"],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    assert "[dry-run]" in install_proc.stdout

    models_proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "models.py"), "show", "--json"],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    assert json.loads(models_proc.stdout)["effective_preset"] == "balanced"
