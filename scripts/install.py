# /// script
# requires-python = ">=3.12"
# ///
"""Personal-machine installer for Paddock Phase 1 platform adapters."""

from __future__ import annotations

import argparse
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


def _copy_plugin_tree(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(
        src,
        dst,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", ".pytest_cache"),
    )


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
