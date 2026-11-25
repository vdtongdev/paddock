"""Unified top-level CLI entrypoint for Paddock (`install`, `uninstall`, `update`, `status`, `models`)."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from core.config import models
from paddock import __version__, installer, updater

_PLATFORM_CHOICES: tuple[str, ...] = ("antigravity", "claude", "all")


def _resolve_cli_platform(
    positional_platform: str | None,
    flag_platform: str | None,
) -> str | None:
    if positional_platform is not None and flag_platform is not None:
        if positional_platform != flag_platform:
            raise ValueError(
                f"Conflicting platform arguments: {positional_platform!r} vs --platform {flag_platform!r}"
            )
        return flag_platform
    return flag_platform if flag_platform is not None else positional_platform


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="paddock",
        description="Multi-platform orchestration loop and unified ~/.paddock/ typed memory suite.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Model configuration:\n"
            "  paddock models <subcommand> ...   Forward directly to model configuration CLI\n"
            "  paddock models --help             Show all model configuration commands\n\n"
            "Examples:\n"
            "  paddock install\n"
            "  paddock status --json\n"
            "  paddock update --check\n"
            "  paddock models show --json\n"
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"paddock {__version__}",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_install = sub.add_parser(
        "install",
        help="Install or apply Paddock plugins for Antigravity and Claude Code",
    )
    p_install.add_argument(
        "positional_platform",
        nargs="?",
        choices=_PLATFORM_CHOICES,
        default=None,
        metavar="target",
        help="Optional target platform (antigravity, claude, all); auto-detected if omitted",
    )
    p_install.add_argument(
        "--platform",
        choices=_PLATFORM_CHOICES,
        default=None,
        dest="platform_flag",
        help="Target platform (antigravity, claude, all)",
    )
    p_install.add_argument("--dry-run", action="store_true", help="Preview actions without making changes")
    p_install.add_argument(
        "--uninstall",
        action="store_true",
        help="Uninstall selected or auto-detected platform plugins",
    )
    p_install.add_argument(
        "--update",
        action="store_true",
        help="Update existing platform plugin installations",
    )
    p_install.add_argument(
        "--target",
        default=None,
        help="Explicit installed plugin or agents/ directory (requires single platform)",
    )
    p_install.add_argument("--home", default=None, help="Override user home directory")
    p_install.add_argument("--repo", default=None, help="Repository root override")
    p_install.add_argument("--json", action="store_true", help="Emit JSON output")

    p_uninstall = sub.add_parser(
        "uninstall",
        help="Uninstall Paddock platform plugins (preserves ~/.paddock/)",
    )
    p_uninstall.add_argument(
        "positional_platform",
        nargs="?",
        choices=_PLATFORM_CHOICES,
        default=None,
        metavar="target",
        help="Optional target platform (antigravity, claude, all); auto-detected if omitted",
    )
    p_uninstall.add_argument(
        "--platform",
        choices=_PLATFORM_CHOICES,
        default=None,
        dest="platform_flag",
        help="Target platform (antigravity, claude, all)",
    )
    p_uninstall.add_argument("--dry-run", action="store_true", help="Preview actions without removing files")
    p_uninstall.add_argument("--home", default=None, help="Override user home directory")
    p_uninstall.add_argument("--repo", default=None, help="Repository root override")
    p_uninstall.add_argument("--json", action="store_true", help="Emit JSON output")

    p_update = sub.add_parser(
        "update",
        help="Upgrade Paddock CLI from GitHub Releases and sync platform plugins",
    )
    p_update.add_argument("--check", action="store_true", help="Check whether a newer release is available")
    p_update.add_argument("--version", default=None, help="Install specific release tag (e.g. v0.2.0)")
    p_update.add_argument(
        "--main",
        action="store_true",
        dest="use_main",
        help="Target main branch instead of latest GitHub Release",
    )
    p_update.add_argument(
        "--platform",
        choices=_PLATFORM_CHOICES,
        default=None,
        help="Limit plugin sync to specific platform",
    )
    p_update.add_argument(
        "--plugins-only",
        action="store_true",
        help="Sync installed platform plugins from current package without running uv",
    )
    p_update.add_argument("--dry-run", action="store_true", help="Preview update without running uv or modifying files")
    p_update.add_argument("--home", default=None, help="Override user home directory")
    p_update.add_argument("--json", action="store_true", help="Emit JSON output")

    p_status = sub.add_parser(
        "status",
        help="Show CLI version, source mode, installed plugins, models, and tools",
    )
    p_status.add_argument("--cwd", default=".", help="Workspace path for project models.json lookup")
    p_status.add_argument("--home", default=None, help="Override user home directory")
    p_status.add_argument("--json", action="store_true", help="Emit JSON output")

    sub.add_parser(
        "models",
        help="Configure and inspect multi-platform agent models (run `paddock models --help`)",
        add_help=False,
    )

    return parser


def _print_result(res: dict[str, Any], *, use_json: bool) -> None:
    if use_json:
        print(json.dumps(res, indent=2, ensure_ascii=False))
    else:
        for line in res.get("messages", []):
            print(line)


def main(argv: list[str] | None = None) -> int:
    """Run the unified `paddock` CLI."""
    args_list = list(sys.argv[1:] if argv is None else argv)

    if args_list and args_list[0] == "models":
        try:
            return int(models.main(args_list[1:]))
        except SystemExit as exc:
            code = exc.code
            if code is None:
                return 0
            return code if isinstance(code, int) else 1

    parser = _build_parser()
    try:
        args = parser.parse_args(args_list)
    except SystemExit as exc:
        code = exc.code
        if code is None:
            return 0
        return code if isinstance(code, int) else 1

    use_json = bool(getattr(args, "json", False))

    try:
        if args.command == "install":
            platform = _resolve_cli_platform(args.positional_platform, args.platform_flag)
            if args.uninstall:
                res = installer.uninstall_targets(
                    platform=platform,
                    home_dir=args.home,
                    target=args.target,
                    dry_run=args.dry_run,
                    repo=args.repo,
                )
            else:
                res = installer.install_targets(
                    platform=platform,
                    home_dir=args.home,
                    target=args.target,
                    dry_run=args.dry_run,
                    repo=args.repo,
                    update_existing=args.update,
                )
            _print_result(res, use_json=use_json)
            return 0

        if args.command == "uninstall":
            platform = _resolve_cli_platform(args.positional_platform, args.platform_flag)
            res = installer.uninstall_targets(
                platform=platform,
                home_dir=args.home,
                dry_run=args.dry_run,
                repo=args.repo,
            )
            _print_result(res, use_json=use_json)
            return 0

        if args.command == "status":
            status = installer.collect_status(cwd=args.cwd, home_dir=args.home)
            if use_json:
                print(json.dumps(status, indent=2, ensure_ascii=False))
            else:
                print(installer.format_status_text(status))
            return 0

        if args.command == "update":
            return updater.run_update(
                check=args.check,
                version=args.version,
                use_main=args.use_main,
                platform=args.platform,
                plugins_only=args.plugins_only,
                dry_run=args.dry_run,
                home_dir=args.home,
                json_output=use_json,
            )

        parser.error(f"Unknown command: {args.command}")
        return 2
    except (ValueError, OSError, RuntimeError, FileNotFoundError) as exc:
        if use_json:
            print(json.dumps({"error": str(exc)}, indent=2, ensure_ascii=False), file=sys.stderr)
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
