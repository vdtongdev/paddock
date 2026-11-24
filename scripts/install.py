# /// script
# requires-python = ">=3.12"
# ///
"""Personal-machine installer for Paddock Phase 1 platform adapters."""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import sys

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import sync  # noqa: E402


def _copy_plugin_tree(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(
        src,
        dst,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", ".pytest_cache"),
    )


def install_antigravity(
    repo_root: Path,
    home_dir: Path,
    *,
    dry_run: bool = False,
    uninstall: bool = False,
) -> list[str]:
    messages: list[str] = []
    dest = home_dir / ".gemini" / "config" / "plugins" / "paddock"

    if uninstall:
        if dest.exists():
            if dry_run:
                messages.append(f"[dry-run] remove {dest}")
            else:
                shutil.rmtree(dest)
                messages.append(f"Removed {dest}")
        else:
            messages.append(f"Already absent: {dest}")
        return messages

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
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        _copy_plugin_tree(src, dest)
        messages.append(f"Installed Antigravity plugin to {dest}")
    messages.append("Manage in Antigravity UI: [paddock](customization://paddock)")
    return messages


def install_claude(
    repo_root: Path,
    *,
    uninstall: bool = False,
) -> list[str]:
    marketplace = repo_root / ".claude-plugin" / "marketplace.json"
    if not marketplace.is_file():
        raise FileNotFoundError(f"Missing Claude marketplace manifest: {marketplace}")

    if uninstall:
        return [
            "Run in terminal to uninstall Claude Code plugin:",
            "  claude plugin uninstall paddock@paddock",
        ]
    return [
        "Run in terminal to register and install Claude Code plugin from local marketplace:",
        f"  claude plugin marketplace add \"{repo_root}\"",
        "  claude plugin install paddock@paddock",
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
    parser.add_argument("--repo", default=None, help="Repository root override")
    args = parser.parse_args(argv)

    repo_root = (
        Path(args.repo).expanduser().resolve()
        if args.repo
        else SCRIPT_DIR.parent
    )
    home_dir = Path(args.home).expanduser().resolve() if args.home else Path.home().resolve()

    try:
        output: list[str] = []
        if args.platform in ("antigravity", "all"):
            output.extend(
                install_antigravity(
                    repo_root,
                    home_dir,
                    dry_run=args.dry_run,
                    uninstall=args.uninstall,
                )
            )
        if args.platform in ("claude", "all"):
            output.extend(install_claude(repo_root, uninstall=args.uninstall))
        for line in output:
            print(line)
        return 0
    except Exception as exc:
        print(f"install error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
