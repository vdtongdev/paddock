"""Cross-platform Paddock installer engine, source resolver, and status collector."""

from __future__ import annotations

import fnmatch
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any

from core.config import agents, models
from paddock import __version__

SUPPORTED_INSTALL_PLATFORMS: tuple[str, ...] = ("antigravity", "claude")
DEFAULT_CLAUDE_MARKETPLACE = "vdtongdev/paddock"
STATUS_TOOLS: tuple[str, ...] = ("uv", "claude", "git", "gh")

_IGNORED_PLUGIN_PATTERNS: tuple[str, ...] = (
    "__pycache__",
    "*.pyc",
    "*.pyo",
    ".pytest_cache",
)

_REQUIRED_ANTIGRAVITY_ASSETS: tuple[str, ...] = (
    "platforms/antigravity/plugin.json",
    "platforms/antigravity/scripts/paddock_core/memory.py",
    "platforms/antigravity/scripts/paddock_core/mcp_server.py",
    "platforms/antigravity/scripts/paddock_core/models.py",
    "platforms/antigravity/scripts/paddock_core/agents.py",
    "core/skills/paddock-models/SKILL.md",
)

_REQUIRED_BUNDLE_ASSETS: tuple[str, ...] = (
    *_REQUIRED_ANTIGRAVITY_ASSETS,
    "platforms/claude/.claude-plugin/plugin.json",
    ".claude-plugin/marketplace.json",
    "platforms/claude/scripts/paddock_core/memory.py",
    "platforms/claude/scripts/paddock_core/mcp_server.py",
    "platforms/claude/scripts/paddock_core/models.py",
    "platforms/claude/scripts/paddock_core/agents.py",
)


def _resolve_home_dir(home_dir: Path | str | None = None) -> Path:
    if home_dir is None:
        raw = Path.home()
        if agents._is_symlink_or_junction(raw):
            raise ValueError(f"Refusing symlinked home directory: {raw}")
        return raw.resolve()
    raw = agents._validate_raw_path_no_traversal(home_dir, label="home")
    if agents._is_symlink_or_junction(raw):
        raise ValueError(f"Refusing symlinked home directory: {raw}")
    return raw.resolve()


def source_mode(root: Path) -> str:
    """Return `'repo'` for a repository checkout or `'bundle'` for a packaged `_bundle/` tree."""
    if agents._is_symlink_or_junction(root):
        raise ValueError(f"Refusing symlinked source directory: {root}")
    if not root.is_dir():
        raise FileNotFoundError(f"Source directory does not exist: {root}")
    if (root / "scripts" / "sync.py").is_file() and (root / "platforms" / "antigravity").is_dir():
        return "repo"
    if (root / "platforms" / "antigravity").is_dir() and (root / "core").is_dir():
        return "bundle"
    if root.name == "_bundle":
        return "bundle"
    raise ValueError(f"Unrecognized Paddock source root layout: {root}")


def resolve_source_root(repo: str | Path | None = None) -> tuple[Path, str]:
    """Resolve `(source_root, mode)` from explicit `--repo`, local checkout, or wheel `_bundle/`."""
    if repo is not None:
        raw = agents._validate_raw_path_no_traversal(repo, label="repo")
        if agents._is_symlink_or_junction(raw):
            raise ValueError(f"Refusing symlinked repository directory: {raw}")
        if raw.exists() and not raw.is_dir():
            raise ValueError(f"Expected repository directory, found non-directory: {raw}")
        if not raw.is_dir():
            raise FileNotFoundError(f"Repository directory does not exist: {raw}")
        return raw.resolve(), "repo"

    pkg_dir = Path(__file__).resolve().parent
    checkout_root = pkg_dir.parent
    if (
        (checkout_root / "scripts" / "sync.py").is_file()
        and (checkout_root / "platforms" / "antigravity").is_dir()
    ):
        return checkout_root.resolve(), "repo"

    bundle_root = pkg_dir / "_bundle"
    if agents._is_symlink_or_junction(bundle_root):
        raise ValueError(f"Refusing symlinked bundle directory: {bundle_root}")
    if bundle_root.is_dir():
        return bundle_root.resolve(), "bundle"

    raise FileNotFoundError(
        "Unable to locate Paddock source root (checked local repo checkout and packaged _bundle/)"
    )


def _check_required_assets(root: Path, required_files: tuple[str, ...]) -> None:
    if agents._is_symlink_or_junction(root):
        raise ValueError(f"Refusing symlinked source directory: {root}")
    if not root.is_dir():
        raise FileNotFoundError(f"Missing Paddock source directory: {root}")

    missing: list[str] = []
    for rel_str in required_files:
        rel_path = Path(rel_str)
        cur = root
        for part in rel_path.parts:
            cur = cur / part
            if agents._is_symlink_or_junction(cur):
                raise ValueError(f"Refusing symlinked bundle entry: {cur}")
        if not cur.is_file():
            missing.append(rel_str)

    agents_dir = root / "core" / "agents"
    if agents._is_symlink_or_junction(root / "core") or agents._is_symlink_or_junction(agents_dir):
        raise ValueError(f"Refusing symlinked canonical agents directory: {agents_dir}")
    if not agents_dir.is_dir():
        missing.append("core/agents/*.md")
    else:
        md_files: list[Path] = []
        for entry in sorted(agents_dir.glob("*.md")):
            if agents._is_symlink_or_junction(entry):
                raise ValueError(f"Refusing symlinked canonical agent file: {entry}")
            if entry.is_file():
                md_files.append(entry)
        if not md_files:
            missing.append("core/agents/*.md")

    if missing:
        raise FileNotFoundError(
            f"Incomplete Paddock bundle at {root}; missing required assets: {', '.join(missing)}"
        )


def verify_bundle_integrity(bundle_root: Path) -> None:
    """Validate that a packaged `_bundle/` tree contains all required pre-synced assets."""
    _check_required_assets(bundle_root, _REQUIRED_BUNDLE_ASSETS)


def _load_sync_module(sync_path: Path):
    resolved = sync_path.resolve()
    for mod_name in ("scripts.sync", "sync", "paddock_repo_sync"):
        existing = sys.modules.get(mod_name)
        existing_file = getattr(existing, "__file__", None)
        if existing is not None and existing_file:
            try:
                if Path(existing_file).resolve() == resolved:
                    return existing
            except OSError:
                pass

    spec = importlib.util.spec_from_file_location("paddock_repo_sync", resolved)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load sync module from {resolved}")
    mod = importlib.util.module_from_spec(spec)
    repo_root_str = str(resolved.parent.parent)
    added_path = False
    if repo_root_str not in sys.path:
        sys.path.insert(0, repo_root_str)
        added_path = True
    try:
        spec.loader.exec_module(mod)
    finally:
        if added_path and repo_root_str in sys.path:
            sys.path.remove(repo_root_str)
    return mod


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
        raise


def _is_ignored_plugin_entry(name: str) -> bool:
    return any(fnmatch.fnmatch(name, pattern) for pattern in _IGNORED_PLUGIN_PATTERNS)


def _has_ignored_plugin_part(rel: Path) -> bool:
    return any(_is_ignored_plugin_entry(part) for part in rel.parts)


def _validate_plugin_dir(path: Path, *, label: str) -> None:
    if agents._is_symlink_or_junction(path):
        raise ValueError(f"Refusing symlinked {label} directory: {path}")
    if path.exists() and not path.is_dir():
        raise ValueError(f"Expected {label} directory, found non-directory: {path}")


def _collect_plugin_tree(src: Path) -> tuple[set[Path], dict[Path, Path]]:
    """Collect expected relative dirs and files from `src`, skipping ignored caches."""
    _validate_plugin_dir(src, label="plugin source")
    if not src.exists():
        raise FileNotFoundError(f"Missing plugin source directory: {src}")

    expected_dirs: set[Path] = set()
    expected_files: dict[Path, Path] = {}
    for dirpath_str, dirnames, filenames in os.walk(src, topdown=True, followlinks=False):
        cur_dir = Path(dirpath_str)
        kept_dirs: list[str] = []
        for dname in sorted(dirnames):
            if _is_ignored_plugin_entry(dname):
                continue
            src_subdir = cur_dir / dname
            if agents._is_symlink_or_junction(src_subdir):
                raise ValueError(f"Refusing symlinked plugin source directory: {src_subdir}")
            expected_dirs.add(src_subdir.relative_to(src))
            kept_dirs.append(dname)
        dirnames[:] = kept_dirs

        for fname in sorted(filenames):
            if _is_ignored_plugin_entry(fname):
                continue
            src_file = cur_dir / fname
            if agents._is_symlink_or_junction(src_file):
                raise ValueError(f"Refusing symlinked plugin source file: {src_file}")
            if not src_file.is_file():
                raise ValueError(f"Expected regular file in plugin source: {src_file}")
            expected_files[src_file.relative_to(src)] = src_file
    return expected_dirs, expected_files


def _prune_stale_plugin_entries(
    dst: Path,
    expected_dirs: set[Path],
    expected_files: dict[Path, Path],
) -> None:
    """Remove stale files and empty stale subdirectories under `dst` without removing `dst`."""
    for dirpath_str, dirnames, filenames in os.walk(dst, topdown=False, followlinks=False):
        cur_dir = Path(dirpath_str)
        if cur_dir != dst and agents._is_symlink_or_junction(cur_dir):
            raise ValueError(f"Refusing symlinked plugin destination directory: {cur_dir}")
        rel_dir = Path(".") if cur_dir == dst else cur_dir.relative_to(dst)
        if rel_dir != Path(".") and _has_ignored_plugin_part(rel_dir):
            continue

        for dname in dirnames:
            if _is_ignored_plugin_entry(dname):
                continue
            dpath = cur_dir / dname
            if agents._is_symlink_or_junction(dpath):
                raise ValueError(f"Refusing symlinked plugin destination directory: {dpath}")

        for fname in filenames:
            if _is_ignored_plugin_entry(fname):
                continue
            fpath = cur_dir / fname
            if agents._is_symlink_or_junction(fpath):
                raise ValueError(f"Refusing symlinked plugin destination file: {fpath}")
            if fpath.relative_to(dst) not in expected_files:
                fpath.unlink()

        if cur_dir != dst and rel_dir not in expected_dirs and not any(cur_dir.iterdir()):
            cur_dir.rmdir()


def _copy_plugin_tree(src: Path, dst: Path) -> None:
    """Sync `src` into `dst` in place so locked `dst` root handles survive reinstall."""
    expected_dirs, expected_files = _collect_plugin_tree(src)
    _validate_plugin_dir(dst, label="plugin destination")
    dst.mkdir(parents=True, exist_ok=True)

    for rel_dir in sorted(expected_dirs, key=lambda p: (len(p.parts), p.as_posix())):
        dst_dir = dst / rel_dir
        _validate_plugin_dir(dst_dir, label="plugin destination")
        dst_dir.mkdir(parents=True, exist_ok=True)

    for rel_file in sorted(expected_files, key=lambda p: p.as_posix()):
        dst_file = dst / rel_file
        if agents._is_symlink_or_junction(dst_file):
            raise ValueError(f"Refusing symlinked plugin destination file: {dst_file}")
        if dst_file.exists() and dst_file.is_dir():
            raise ValueError(f"Expected plugin destination file, found directory: {dst_file}")
        src_bytes = expected_files[rel_file].read_bytes()
        if dst_file.is_file() and dst_file.read_bytes() == src_bytes:
            continue
        _atomic_write_bytes(dst_file, src_bytes)

    _prune_stale_plugin_entries(dst, expected_dirs, expected_files)


def _format_apply_messages(label: str, report: dict[str, Any], *, dry_run: bool) -> list[str]:
    cfg_note = (
        report["global_config_path"]
        if report["global_config_exists"]
        else "built-in balanced defaults"
    )
    lines: list[str] = []
    for entry in report["targets"]:
        agents_dir = entry["agents_dir"]
        if dry_run:
            lines.append(
                f"[dry-run] apply {label} agents ({cfg_note}) -> {agents_dir} "
                f"({len(entry['would_write'])} update(s), {len(entry['unchanged'])} unchanged)"
            )
            for path in entry["would_write"]:
                lines.append(f"[dry-run] write {path}")
        else:
            lines.append(
                f"Applied {label} agents ({cfg_note}) -> {agents_dir} "
                f"({len(entry['written'])} updated, {len(entry['unchanged'])} unchanged)"
            )
    return lines


def detect_platforms(home_dir: Path | str | None = None) -> list[str]:
    """Auto-detect installed/available target platforms (`antigravity`, `claude`)."""
    effective_home = _resolve_home_dir(home_dir)
    detected: list[str] = []
    if (effective_home / ".gemini").is_dir():
        detected.append("antigravity")
    if shutil.which("claude") is not None or (effective_home / ".claude").is_dir():
        detected.append("claude")
    return detected


def install_antigravity(
    repo_root: Path,
    home_dir: Path | None = None,
    *,
    target: Path | str | None = None,
    dry_run: bool = False,
    uninstall: bool = False,
    paddock_home: Path | str | None = None,
) -> list[str]:
    """Install, update, apply models to, or uninstall the Antigravity plugin."""
    effective_home = _resolve_home_dir(home_dir)
    messages: list[str] = []
    dest = effective_home / ".gemini" / "config" / "plugins" / "paddock"

    if uninstall:
        if target is not None:
            raise ValueError("--target cannot be combined with --uninstall")
        agents._check_non_symlink_descendants(effective_home, dest)
        if dest.exists():
            if dry_run:
                messages.append(f"[dry-run] remove {dest}")
            else:
                shutil.rmtree(dest)
                messages.append(f"Removed {dest}")
        else:
            messages.append(f"Already absent: {dest}")
        return messages

    if target is not None:
        report = agents.apply_models_to_targets(
            platform="antigravity",
            home=effective_home,
            target=target,
            dry_run=dry_run,
            paddock_home=paddock_home,
            source=repo_root,
        )
        return _format_apply_messages("Antigravity", report, dry_run=dry_run)

    sync_script = repo_root / "scripts" / "sync.py"
    if sync_script.is_file():
        sync_mod = _load_sync_module(sync_script)
        drift = sync_mod.sync_all(repo_root, check=True)
        if drift:
            raise RuntimeError(
                "Core/platform drift detected; run `python scripts/sync.py` before installing:\n"
                + "\n".join(drift)
            )
    else:
        _check_required_assets(repo_root, _REQUIRED_ANTIGRAVITY_ASSETS)

    src = repo_root / "platforms" / "antigravity"
    if not src.is_dir():
        raise FileNotFoundError(f"Missing Antigravity plugin directory: {src}")

    if dry_run:
        messages.append(f"[dry-run] copy {src} -> {dest}")
        if dest.exists():
            report = agents.apply_models_to_targets(
                platform="antigravity",
                home=effective_home,
                dry_run=True,
                paddock_home=paddock_home,
                source=repo_root,
            )
            messages.extend(_format_apply_messages("Antigravity", report, dry_run=True))
        else:
            messages.append(
                f"[dry-run] render Antigravity agents ({repo_root / 'core' / 'agents'}) -> {dest / 'agents'}"
            )
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        _copy_plugin_tree(src, dest)
        messages.append(f"Installed Antigravity plugin to {dest}")
        report = agents.apply_models_to_targets(
            platform="antigravity",
            home=effective_home,
            dry_run=False,
            paddock_home=paddock_home,
            source=repo_root,
        )
        messages.extend(_format_apply_messages("Antigravity", report, dry_run=False))
    messages.append("Manage in Antigravity UI: [paddock](customization://paddock)")
    return messages


def _validate_marketplace_source(raw_source: str) -> str:
    if not isinstance(raw_source, str) or not raw_source.strip():
        raise ValueError("Marketplace source must be a non-empty string")
    if "\x00" in raw_source or "\n" in raw_source or "\r" in raw_source:
        raise ValueError(f"Control character rejected in marketplace source: {raw_source!r}")
    if raw_source.startswith("-"):
        raise ValueError(f"Invalid leading '-' in marketplace source: {raw_source!r}")
    if raw_source != DEFAULT_CLAUDE_MARKETPLACE and ("/" in raw_source or "\\" in raw_source):
        path = agents._validate_raw_path_no_traversal(raw_source, label="marketplace source")
        if agents._is_symlink_or_junction(path):
            raise ValueError(f"Refusing symlinked marketplace source directory: {path}")
    return raw_source


def _claude_subprocess_env(effective_home: Path) -> dict[str, str] | None:
    default_home = Path.home().resolve()
    if effective_home == default_home:
        return None
    env = os.environ.copy()
    env["HOME"] = str(effective_home)
    env["USERPROFILE"] = str(effective_home)
    return env


def _run_claude(args: list[str], *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        shell=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=120,
    )


def _format_cmd_arg(arg: str) -> str:
    return f'"{arg}"' if any(ch.isspace() for ch in arg) else arg


def install_claude(
    repo_root: Path,
    home_dir: Path | None = None,
    *,
    target: Path | str | None = None,
    dry_run: bool = False,
    uninstall: bool = False,
    paddock_home: Path | str | None = None,
    marketplace_source: str | None = None,
    update_existing: bool = False,
    execute_cli: bool = True,
) -> list[str]:
    """Install, update, apply models to, or uninstall the Claude Code plugin."""
    effective_home = _resolve_home_dir(home_dir)
    marketplace = repo_root / ".claude-plugin" / "marketplace.json"

    if uninstall:
        if target is not None:
            raise ValueError("--target cannot be combined with --uninstall")
        if not marketplace.is_file():
            raise FileNotFoundError(f"Missing Claude marketplace manifest: {marketplace}")
        claude_bin = shutil.which("claude") if execute_cli else None
        if claude_bin is not None and not dry_run:
            env = _claude_subprocess_env(effective_home)
            proc = _run_claude([claude_bin, "plugin", "uninstall", "paddock@paddock"], env=env)
            combined = f"{proc.stdout}\n{proc.stderr}".lower()
            if proc.returncode == 0:
                return ["Uninstalled Claude Code plugin paddock@paddock (preserved ~/.paddock/)."]
            if "not installed" in combined or "not found" in combined:
                return ["Claude Code plugin paddock@paddock is already absent."]
            detail = (proc.stderr or proc.stdout).strip() or f"exit {proc.returncode}"
            raise RuntimeError(f"claude plugin uninstall paddock@paddock failed: {detail}")
        header = (
            "[dry-run] Run in terminal to uninstall Claude Code plugin:"
            if dry_run
            else "Run in terminal to uninstall Claude Code plugin:"
        )
        return [header, "  claude plugin uninstall paddock@paddock"]

    if target is not None:
        report = agents.apply_models_to_targets(
            platform="claude",
            home=effective_home,
            target=target,
            dry_run=dry_run,
            paddock_home=paddock_home,
            source=repo_root,
        )
        return _format_apply_messages("Claude Code", report, dry_run=dry_run)

    if not marketplace.is_file():
        raise FileNotFoundError(f"Missing Claude marketplace manifest: {marketplace}")

    marketplace_ref = (
        _validate_marketplace_source(marketplace_source)
        if marketplace_source is not None
        else DEFAULT_CLAUDE_MARKETPLACE
    )
    quoted_ref = _format_cmd_arg(marketplace_ref)

    claude_bin = shutil.which("claude") if execute_cli else None
    if claude_bin is not None and not dry_run:
        env = _claude_subprocess_env(effective_home)
        messages: list[str] = []
        existing_targets = agents._discover_claude_targets(effective_home)

        add_proc = _run_claude([claude_bin, "plugin", "marketplace", "add", marketplace_ref], env=env)
        add_output = f"{add_proc.stdout}\n{add_proc.stderr}"
        if add_proc.returncode == 0:
            messages.append(f"Registered Claude Code marketplace ({marketplace_ref})")
        elif "already" in add_output.lower():
            upd_mkt_proc = _run_claude([claude_bin, "plugin", "marketplace", "update", "paddock"], env=env)
            if upd_mkt_proc.returncode != 0:
                detail = (upd_mkt_proc.stderr or upd_mkt_proc.stdout).strip() or f"exit {upd_mkt_proc.returncode}"
                raise RuntimeError(f"claude plugin marketplace update paddock failed: {detail}")
            messages.append("Updated Claude Code marketplace (paddock)")
        else:
            detail = (add_proc.stderr or add_proc.stdout).strip() or f"exit {add_proc.returncode}"
            raise RuntimeError(f"claude plugin marketplace add {marketplace_ref} failed: {detail}")

        should_update = update_existing or bool(existing_targets)
        if should_update:
            plugin_proc = _run_claude([claude_bin, "plugin", "update", "paddock@paddock"], env=env)
            if plugin_proc.returncode != 0:
                detail = (plugin_proc.stderr or plugin_proc.stdout).strip() or f"exit {plugin_proc.returncode}"
                raise RuntimeError(f"claude plugin update paddock@paddock failed: {detail}")
            messages.append("Updated Claude Code plugin (paddock@paddock)")
        else:
            plugin_proc = _run_claude([claude_bin, "plugin", "install", "paddock@paddock"], env=env)
            plugin_output = f"{plugin_proc.stdout}\n{plugin_proc.stderr}"
            if plugin_proc.returncode == 0:
                messages.append("Installed Claude Code plugin (paddock@paddock)")
            elif "already" in plugin_output.lower():
                upd_plugin_proc = _run_claude([claude_bin, "plugin", "update", "paddock@paddock"], env=env)
                if upd_plugin_proc.returncode != 0:
                    detail = (upd_plugin_proc.stderr or upd_plugin_proc.stdout).strip() or f"exit {upd_plugin_proc.returncode}"
                    raise RuntimeError(f"claude plugin update paddock@paddock failed: {detail}")
                messages.append("Updated Claude Code plugin (paddock@paddock)")
            else:
                detail = (plugin_proc.stderr or plugin_proc.stdout).strip() or f"exit {plugin_proc.returncode}"
                raise RuntimeError(f"claude plugin install paddock@paddock failed: {detail}")

        report = agents.apply_models_to_targets(
            platform="claude",
            home=effective_home,
            target=None,
            dry_run=False,
            paddock_home=paddock_home,
            source=repo_root,
        )
        if report["targets"]:
            messages.extend(_format_apply_messages("Claude Code", report, dry_run=False))
        return messages

    report = agents.apply_models_to_targets(
        platform="claude",
        home=effective_home,
        target=None,
        dry_run=dry_run,
        paddock_home=paddock_home,
        source=repo_root,
    )
    if dry_run:
        messages = []
        if update_existing or report["targets"]:
            messages.append("[dry-run] run: claude plugin marketplace update paddock")
            messages.append("[dry-run] run: claude plugin update paddock@paddock")
        else:
            messages.append(f"[dry-run] run: claude plugin marketplace add {quoted_ref}")
            messages.append("[dry-run] run: claude plugin install paddock@paddock")
        if report["targets"]:
            messages.extend(_format_apply_messages("Claude Code", report, dry_run=True))
        return messages

    if report["targets"]:
        apply_lines = _format_apply_messages("Claude Code", report, dry_run=False)
        if update_existing:
            return [
                "Run in terminal to update Claude Code plugin:",
                "  claude plugin marketplace update paddock",
                "  claude plugin update paddock@paddock",
                *apply_lines,
            ]
        return apply_lines

    return [
        f"No installed Claude Code paddock plugin targets found under {effective_home / '.claude' / 'plugins'}.",
        "Run in terminal to register and install Claude Code plugin:",
        f"  claude plugin marketplace add {quoted_ref}",
        "  claude plugin install paddock@paddock",
        "After installing, re-run `paddock install claude` to apply ~/.paddock/models.json.",
    ]


def resolve_requested_platforms(
    platform: str | None = None,
    home_dir: Path | str | None = None,
) -> tuple[list[str], bool]:
    """Resolve requested platforms `(antigravity, claude)` and whether auto-detection ran."""
    effective_home = _resolve_home_dir(home_dir)
    if platform is None:
        return detect_platforms(effective_home), True
    if platform == "all":
        return list(SUPPORTED_INSTALL_PLATFORMS), False
    if platform in SUPPORTED_INSTALL_PLATFORMS:
        return [platform], False
    raise ValueError(
        f"Unsupported platform {platform!r}; expected 'antigravity', 'claude', or 'all'"
    )


def install_targets(
    platform: str | None = None,
    home_dir: Path | str | None = None,
    *,
    target: Path | str | None = None,
    dry_run: bool = False,
    paddock_home: Path | str | None = None,
    repo: Path | str | None = None,
    update_existing: bool = False,
    execute_claude_cli: bool = True,
) -> dict[str, Any]:
    """Install or update selected/auto-detected platforms from repo checkout or wheel `_bundle/`."""
    if target is not None and platform not in SUPPORTED_INSTALL_PLATFORMS:
        raise ValueError("--target requires an explicit single --platform ('antigravity' or 'claude')")

    effective_home = _resolve_home_dir(home_dir)
    source_root, mode = resolve_source_root(repo)
    selected, auto_detected = resolve_requested_platforms(platform, effective_home)

    if not selected:
        return {
            "action": "install",
            "platforms": [],
            "auto_detected": auto_detected,
            "detected": False,
            "dry_run": dry_run,
            "home_dir": str(effective_home),
            "source_mode": mode,
            "source_root": str(source_root),
            "results": {},
            "messages": [
                f"No supported platforms detected under {effective_home} (checked ~/.gemini and claude / ~/.claude).",
                "Specify an explicit target platform:",
                "  paddock install antigravity",
                "  paddock install claude",
                "  paddock install all",
            ],
        }

    if mode == "bundle" and target is None:
        verify_bundle_integrity(source_root)

    marketplace_source = str(source_root) if repo is not None else None
    results: dict[str, list[str]] = {}
    messages: list[str] = []
    for plat in selected:
        if plat == "antigravity":
            plat_msgs = install_antigravity(
                source_root,
                effective_home,
                target=target,
                dry_run=dry_run,
                uninstall=False,
                paddock_home=paddock_home,
            )
        else:
            plat_msgs = install_claude(
                source_root,
                effective_home,
                target=target,
                dry_run=dry_run,
                uninstall=False,
                paddock_home=paddock_home,
                marketplace_source=marketplace_source,
                update_existing=update_existing,
                execute_cli=execute_claude_cli,
            )
        results[plat] = plat_msgs
        messages.extend(plat_msgs)

    return {
        "action": "install",
        "platforms": selected,
        "auto_detected": auto_detected,
        "detected": True,
        "dry_run": dry_run,
        "home_dir": str(effective_home),
        "source_mode": mode,
        "source_root": str(source_root),
        "results": results,
        "messages": messages,
    }


def uninstall_targets(
    platform: str | None = None,
    home_dir: Path | str | None = None,
    *,
    target: Path | str | None = None,
    dry_run: bool = False,
    paddock_home: Path | str | None = None,
    repo: Path | str | None = None,
    execute_claude_cli: bool = True,
) -> dict[str, Any]:
    """Uninstall selected/auto-detected platform plugins while preserving `~/.paddock/`."""
    if target is not None:
        raise ValueError("--target cannot be combined with --uninstall")

    effective_home = _resolve_home_dir(home_dir)
    source_root, mode = resolve_source_root(repo)
    selected, auto_detected = resolve_requested_platforms(platform, effective_home)

    if not selected:
        return {
            "action": "uninstall",
            "platforms": [],
            "auto_detected": auto_detected,
            "detected": False,
            "dry_run": dry_run,
            "home_dir": str(effective_home),
            "source_mode": mode,
            "source_root": str(source_root),
            "results": {},
            "messages": [
                f"No supported platforms detected under {effective_home} (checked ~/.gemini and claude / ~/.claude).",
                "Specify an explicit target platform:",
                "  paddock uninstall antigravity",
                "  paddock uninstall claude",
                "  paddock uninstall all",
            ],
        }

    results: dict[str, list[str]] = {}
    messages: list[str] = []
    for plat in selected:
        if plat == "antigravity":
            plat_msgs = install_antigravity(
                source_root,
                effective_home,
                dry_run=dry_run,
                uninstall=True,
                paddock_home=paddock_home,
            )
        else:
            plat_msgs = install_claude(
                source_root,
                effective_home,
                dry_run=dry_run,
                uninstall=True,
                paddock_home=paddock_home,
                execute_cli=execute_claude_cli,
            )
        results[plat] = plat_msgs
        messages.extend(plat_msgs)

    return {
        "action": "uninstall",
        "platforms": selected,
        "auto_detected": auto_detected,
        "detected": True,
        "dry_run": dry_run,
        "home_dir": str(effective_home),
        "source_mode": mode,
        "source_root": str(source_root),
        "results": results,
        "messages": messages,
    }


def _read_plugin_manifest_version(manifest_path: Path) -> str | None:
    if agents._is_symlink_or_junction(manifest_path) or not manifest_path.is_file():
        return None
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if isinstance(data, dict) and isinstance(data.get("version"), str):
        return data["version"]
    return None


def collect_status(
    cwd: str | Path = ".",
    home_dir: Path | None = None,
    paddock_home: Path | str | None = None,
) -> dict[str, Any]:
    """Collect CLI version, source mode, installed platform plugins, models config, and tools."""
    effective_home = _resolve_home_dir(home_dir)
    cwd_path = Path(cwd).expanduser().resolve()
    source_root, mode = resolve_source_root()

    agy_dir = effective_home / ".gemini" / "config" / "plugins" / "paddock"
    agy_installed = False
    agy_version: str | None = None
    agy_error: str | None = None
    try:
        agents._check_non_symlink_descendants(effective_home, agy_dir)
        if agy_dir.is_dir():
            agy_installed = True
            agy_version = _read_plugin_manifest_version(agy_dir / "plugin.json")
    except (OSError, ValueError) as exc:
        agy_error = str(exc)

    agy_status: dict[str, Any] = {
        "detected": (effective_home / ".gemini").is_dir(),
        "installed": agy_installed,
        "path": str(agy_dir),
        "version": agy_version,
    }
    if agy_error is not None:
        agy_status["error"] = agy_error

    claude_installs: list[dict[str, str | None]] = []
    claude_error: str | None = None
    try:
        for agents_dir in agents._discover_claude_targets(effective_home):
            plugin_dir = agents_dir.parent
            claude_installs.append(
                {
                    "path": str(plugin_dir),
                    "version": _read_plugin_manifest_version(plugin_dir / ".claude-plugin" / "plugin.json"),
                }
            )
    except (OSError, ValueError) as exc:
        claude_error = str(exc)

    claude_status: dict[str, Any] = {
        "detected": shutil.which("claude") is not None or (effective_home / ".claude").is_dir(),
        "installed": bool(claude_installs),
        "path": claude_installs[0]["path"] if claude_installs else None,
        "version": claude_installs[0]["version"] if claude_installs else None,
        "installs": claude_installs,
    }
    if claude_error is not None:
        claude_status["error"] = claude_error

    cfg_home = agents._resolve_apply_config_home(user_home=effective_home, paddock_home=paddock_home)
    models_summary = models.resolve_all(cwd_path, home=cfg_home)

    tools: dict[str, dict[str, Any]] = {}
    for tool_name in STATUS_TOOLS:
        tool_path = shutil.which(tool_name)
        tools[tool_name] = {
            "available": tool_path is not None,
            "path": tool_path,
        }

    return {
        "version": __version__,
        "source_mode": mode,
        "source_root": str(source_root),
        "home_dir": str(effective_home),
        "cwd": str(cwd_path),
        "platforms": {
            "antigravity": agy_status,
            "claude": claude_status,
        },
        "models": {
            "preset": models_summary["effective_preset"],
            "repo_slug": models_summary["repo_slug"],
            "global_path": models_summary["global_path"],
            "global_exists": models_summary["global_exists"],
            "project_path": models_summary["project_path"],
            "project_exists": models_summary["project_exists"],
        },
        "tools": tools,
    }


def format_status_text(status: dict[str, Any]) -> str:
    """Format `collect_status()` mapping as human-readable terminal text."""
    lines = [
        f"paddock {status['version']} (source: {status['source_mode']} @ {status['source_root']})",
        f"Home: {status['home_dir']}",
        f"Workspace: {status['cwd']}",
        "",
        "Platforms:",
    ]
    agy = status["platforms"]["antigravity"]
    agy_state = f"installed (v{agy['version'] or 'unknown'})" if agy["installed"] else (
        "detected, not installed" if agy["detected"] else "not detected"
    )
    lines.append(f"  antigravity: {agy_state} -> {agy['path']}")

    claude = status["platforms"]["claude"]
    if claude["installed"]:
        claude_state = f"installed (v{claude['version'] or 'unknown'}) -> {claude['path']}"
    elif claude["detected"]:
        claude_state = "detected, not installed"
    else:
        claude_state = "not detected"
    lines.append(f"  claude:      {claude_state}")

    mod = status["models"]
    lines.extend(
        [
            "",
            "Models:",
            f"  preset:  {mod['preset']}",
            f"  slug:    {mod['repo_slug']}",
            f"  global:  {mod['global_path']} ({'loaded' if mod['global_exists'] else 'default'})",
            f"  project: {mod['project_path']} ({'loaded' if mod['project_exists'] else 'none'})",
            "",
            "Tools:",
        ]
    )
    for tool_name, info in status["tools"].items():
        tool_state = info["path"] if info["available"] else "missing"
        lines.append(f"  {tool_name:<8} {tool_state}")
    return "\n".join(lines)
