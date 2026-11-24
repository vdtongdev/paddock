# /// script
# requires-python = ">=3.12"
# ///
"""Personal-machine installer for Paddock Phase 1 platform adapters."""

from __future__ import annotations

import argparse
import fnmatch
import os
from pathlib import Path
import shutil
import sys
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
for _path in (str(SCRIPT_DIR), str(REPO_ROOT)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from core.config import agents  # noqa: E402
import sync  # noqa: E402

_IGNORED_PLUGIN_PATTERNS: tuple[str, ...] = (
    "__pycache__",
    "*.pyc",
    "*.pyo",
    ".pytest_cache",
)


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
        sync._atomic_write_bytes(dst_file, src_bytes)

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


def install_antigravity(
    repo_root: Path,
    home_dir: Path | None = None,
    *,
    target: Path | str | None = None,
    dry_run: bool = False,
    uninstall: bool = False,
    paddock_home: Path | str | None = None,
) -> list[str]:
    effective_home = home_dir if home_dir is not None else Path.home().resolve()
    messages: list[str] = []
    dest = effective_home / ".gemini" / "config" / "plugins" / "paddock"

    if uninstall:
        if target is not None:
            raise ValueError("--target cannot be combined with --uninstall")
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

    drift = sync.sync_all(repo_root, check=True)
    if drift:
        raise RuntimeError(
            "Core/platform drift detected; run `python scripts/sync.py` before installing:\n"
            + "\n".join(drift)
        )

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


def install_claude(
    repo_root: Path,
    home_dir: Path | None = None,
    *,
    target: Path | str | None = None,
    dry_run: bool = False,
    uninstall: bool = False,
    paddock_home: Path | str | None = None,
) -> list[str]:
    if uninstall:
        if target is not None:
            raise ValueError("--target cannot be combined with --uninstall")
        marketplace = repo_root / ".claude-plugin" / "marketplace.json"
        if not marketplace.is_file():
            raise FileNotFoundError(f"Missing Claude marketplace manifest: {marketplace}")
        return [
            "Run in terminal to uninstall Claude Code plugin:",
            "  claude plugin uninstall paddock@paddock",
        ]

    effective_home = home_dir if home_dir is not None else Path.home().resolve()
    report = agents.apply_models_to_targets(
        platform="claude",
        home=effective_home,
        target=target,
        dry_run=dry_run,
        paddock_home=paddock_home,
        source=repo_root,
    )
    if report["targets"]:
        return _format_apply_messages("Claude Code", report, dry_run=dry_run)

    marketplace = repo_root / ".claude-plugin" / "marketplace.json"
    if not marketplace.is_file():
        raise FileNotFoundError(f"Missing Claude marketplace manifest: {marketplace}")
    return [
        f"No installed Claude Code paddock plugin targets found under {effective_home / '.claude' / 'plugins'}.",
        "Run in terminal to register and install Claude Code plugin from local marketplace:",
        f'  claude plugin marketplace add "{repo_root}"',
        "  claude plugin install paddock@paddock",
        "After installing, re-run `python scripts/install.py --platform claude` to apply ~/.paddock/models.json.",
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install or uninstall Paddock platform adapters")
    parser.add_argument(
        "--platform",
        choices=("antigravity", "claude", "all"),
        required=True,
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--home", default=None, help="Override user home directory")
    parser.add_argument(
        "--target",
        default=None,
        help="Explicit installed plugin or agents/ directory (requires single --platform)",
    )
    parser.add_argument("--repo", default=None, help="Repository root override")
    args = parser.parse_args(argv)

    repo_root = (
        Path(args.repo).expanduser().resolve()
        if args.repo
        else SCRIPT_DIR.parent
    )
    home_dir = Path(args.home).expanduser().resolve() if args.home else Path.home().resolve()

    try:
        if args.target is not None and args.platform == "all":
            raise ValueError("--target requires an explicit single --platform ('antigravity' or 'claude')")
        if args.target is not None and args.uninstall:
            raise ValueError("--target cannot be combined with --uninstall")

        output: list[str] = []
        if args.platform in ("antigravity", "all"):
            output.extend(
                install_antigravity(
                    repo_root,
                    home_dir,
                    target=args.target,
                    dry_run=args.dry_run,
                    uninstall=args.uninstall,
                )
            )
        if args.platform in ("claude", "all"):
            output.extend(
                install_claude(
                    repo_root,
                    home_dir,
                    target=args.target,
                    dry_run=args.dry_run,
                    uninstall=args.uninstall,
                )
            )
        for line in output:
            print(line)
        return 0
    except Exception as exc:
        print(f"install error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
