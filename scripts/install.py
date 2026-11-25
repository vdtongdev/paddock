# /// script
# requires-python = ">=3.12"
# ///
"""Backward-compatible installer wrapper delegating to `paddock.installer`."""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import sys

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import agents  # noqa: E402
from paddock import installer  # noqa: E402

_copy_plugin_tree = installer._copy_plugin_tree


def install_antigravity(
    repo_root: Path,
    home_dir: Path | None = None,
    *,
    target: Path | str | None = None,
    dry_run: bool = False,
    uninstall: bool = False,
    paddock_home: Path | str | None = None,
) -> list[str]:
    """Delegate Antigravity installation to `paddock.installer.install_antigravity`."""
    return installer.install_antigravity(
        repo_root,
        home_dir,
        target=target,
        dry_run=dry_run,
        uninstall=uninstall,
        paddock_home=paddock_home,
    )


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
    execute_cli: bool = False,
) -> list[str]:
    """Delegate Claude Code profile application/guidance to `paddock.installer.install_claude`."""
    return installer.install_claude(
        repo_root,
        home_dir,
        target=target,
        dry_run=dry_run,
        uninstall=uninstall,
        paddock_home=paddock_home,
        marketplace_source=marketplace_source,
        update_existing=update_existing,
        execute_cli=execute_cli,
    )


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

    try:
        if args.target is not None and args.platform == "all":
            raise ValueError("--target requires an explicit single --platform ('antigravity' or 'claude')")
        if args.target is not None and args.uninstall:
            raise ValueError("--target cannot be combined with --uninstall")

        source_root, _ = installer.resolve_source_root(args.repo)
        home_dir = installer._resolve_home_dir(args.home)
        marketplace_source = str(source_root) if args.repo is not None else None

        output: list[str] = []
        if args.platform in ("antigravity", "all"):
            output.extend(
                install_antigravity(
                    source_root,
                    home_dir,
                    target=args.target,
                    dry_run=args.dry_run,
                    uninstall=args.uninstall,
                )
            )
        if args.platform in ("claude", "all"):
            output.extend(
                install_claude(
                    source_root,
                    home_dir,
                    target=args.target,
                    dry_run=args.dry_run,
                    uninstall=args.uninstall,
                    marketplace_source=marketplace_source,
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
