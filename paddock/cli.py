"""Unified top-level CLI entrypoint for Paddock (`install`, `uninstall`, `update`, `status`, `models`, `worktree`)."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from core.config import models
from core.git import worktree
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
            "  paddock worktree list --json\n"
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

    p_worktree = sub.add_parser(
        "worktree",
        help="Create, list, and safely remove isolated Paddock git worktrees",
    )
    wt_sub = p_worktree.add_subparsers(dest="worktree_command", required=True)

    p_wt_create = wt_sub.add_parser(
        "create",
        help="Idempotently create or reuse .worktrees/<slug> on <prefix>/<slug>",
    )
    p_wt_create.add_argument("slug", help="Worktree slug under .worktrees/")
    p_wt_create.add_argument(
        "--prefix",
        default="feat",
        help=f"Branch prefix (<prefix>/<slug>; one of {', '.join(worktree.VALID_PREFIXES)}; default: feat)",
    )
    p_wt_create.add_argument(
        "--base",
        default=None,
        metavar="REF",
        help="Base git ref (defaults to main, master, or HEAD)",
    )
    p_wt_create.add_argument(
        "--plan-path",
        default=None,
        metavar="PATH",
        help="Plan file path inside repository to copy if untracked or missing",
    )
    p_wt_create.add_argument(
        "--copy",
        dest="copy_paths",
        action="extend",
        nargs="+",
        default=None,
        metavar="PATH",
        help="Additional repo-relative file path(s) to copy if untracked or missing",
    )
    p_wt_create.add_argument(
        "--no-codegraph",
        dest="init_codegraph",
        action="store_false",
        default=True,
        help="Skip running `codegraph init -y` in the created worktree",
    )
    p_wt_create.add_argument("--cwd", default=".", metavar="DIR", help="Repository path (default: .)")
    p_wt_create.add_argument("--json", action="store_true", help="Emit JSON output")

    p_wt_list = wt_sub.add_parser(
        "list",
        help="List registered git worktrees for the repository",
    )
    p_wt_list.add_argument("--cwd", default=".", metavar="DIR", help="Repository path (default: .)")
    p_wt_list.add_argument("--json", action="store_true", help="Emit JSON output")

    p_wt_remove = wt_sub.add_parser(
        "remove",
        help="Non-force remove registered .worktrees/<slug> checkout",
    )
    p_wt_remove.add_argument("slug", help="Paddock-managed worktree slug under .worktrees/")
    p_wt_remove.add_argument(
        "--delete-branch",
        action="store_true",
        help="Also delete the worktree branch via non-force `git branch -d`",
    )
    p_wt_remove.add_argument("--cwd", default=".", metavar="DIR", help="Repository path (default: .)")
    p_wt_remove.add_argument("--json", action="store_true", help="Emit JSON output")

    return parser


def _format_worktree_create_text(res: dict[str, Any]) -> str:
    """Format `worktree.create_worktree()` result as concise terminal summary lines."""
    if res.get("reused"):
        headline = f"Reused worktree: {res['worktree_path']} (branch: {res['branch']})"
    elif res.get("reused_branch"):
        headline = f"Created worktree: {res['worktree_path']} (branch: {res['branch']}, attached existing)"
    else:
        headline = f"Created worktree: {res['worktree_path']} (branch: {res['branch']}, base: {res['base']})"
    lines = [headline]
    if res.get("exclude_updated"):
        lines.append("Updated .git/info/exclude with .worktrees/")
    copied = res.get("copied_files") or []
    if copied:
        lines.append(f"Copied files: {', '.join(copied)}")
    lines.append(f"CodeGraph: {res.get('codegraph_status', 'unknown')}")
    return "\n".join(lines)


def _format_worktree_list_text(res: dict[str, Any]) -> str:
    """Format `worktree.list_worktrees()` result as concise terminal summary lines."""
    lines = [f"Repository: {res['repo_root']}"]
    entries = res.get("worktrees") or []
    if not entries:
        lines.append("No worktrees found.")
        return "\n".join(lines)
    for entry in entries:
        tags: list[str] = []
        if entry.get("is_main"):
            tags.append("main")
        elif entry.get("is_paddock_managed"):
            tags.append(f"paddock:{entry.get('slug')}")
        if entry.get("detached"):
            tags.append("detached")
        if entry.get("locked"):
            tags.append("locked")
        if entry.get("prunable"):
            tags.append("prunable")
        if entry.get("bare"):
            tags.append("bare")
        branch = entry.get("branch") or "(detached)"
        tag_str = f" [{', '.join(tags)}]" if tags else ""
        lines.append(f"  {branch:<28} {entry['path']}{tag_str}")
    return "\n".join(lines)


def _format_worktree_remove_text(res: dict[str, Any]) -> str:
    """Format `worktree.remove_worktree()` result (including branch warnings) as terminal lines."""
    branch = res.get("branch")
    branch_desc = f"branch: {branch}" if branch else "detached HEAD"
    lines = [f"Removed worktree: {res['worktree_path']} (slug: {res['slug']}, {branch_desc})"]
    if res.get("branch_deleted") and branch:
        lines.append(f"Deleted branch: {branch}")
    warning = res.get("branch_delete_warning")
    if warning:
        lines.append(f"Warning: {warning}")
    return "\n".join(lines)


def _run_worktree_command(
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
    *,
    use_json: bool,
) -> int:
    """Dispatch `paddock worktree <create|list|remove>` and print JSON or text summary."""
    if args.worktree_command == "create":
        res = worktree.create_worktree(
            args.cwd,
            args.slug,
            prefix=args.prefix,
            base=args.base,
            plan_path=args.plan_path,
            copy_paths=args.copy_paths,
            init_codegraph=args.init_codegraph,
        )
        formatter = _format_worktree_create_text
    elif args.worktree_command == "list":
        res = worktree.list_worktrees(args.cwd)
        formatter = _format_worktree_list_text
    elif args.worktree_command == "remove":
        res = worktree.remove_worktree(
            args.cwd,
            args.slug,
            delete_branch=args.delete_branch,
        )
        formatter = _format_worktree_remove_text
    else:
        parser.error(f"Unknown worktree command: {args.worktree_command}")
        return 2

    if use_json:
        print(json.dumps(res, indent=2, ensure_ascii=False))
    else:
        print(formatter(res))
    return 0


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

        if args.command == "worktree":
            return _run_worktree_command(args, parser, use_json=use_json)

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
