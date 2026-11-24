# /// script
# requires-python = ">=3.12"
# ///
"""Sync platform-agnostic core skills, config modules, and canonical agents into platform adapters."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import tempfile

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import agents, models  # noqa: E402

CORE_MEMORY_FILES = ("memory.py", "mcp_server.py")
CORE_CONFIG_FILES = ("models.py", "agents.py")


def _normalize_lf(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _is_text_file(path: Path) -> bool:
    return path.suffix.lower() in {".md", ".py", ".json", ".toml", ".txt", ".yaml", ".yml"}


def _read_normalized(path: Path) -> bytes:
    raw = path.read_bytes()
    if _is_text_file(path):
        return _normalize_lf(raw)
    return raw


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


def _sync_bytes(src_bytes: bytes, dst: Path, *, src_label: str, check: bool) -> str | None:
    if dst.is_file():
        dst_bytes = _read_normalized(dst)
        if dst_bytes == src_bytes:
            return None
    if check:
        return f"drift: {dst} != {src_label}"
    _atomic_write_bytes(dst, src_bytes)
    return str(dst)


def _sync_file(src: Path, dst: Path, *, check: bool) -> str | None:
    return _sync_bytes(_read_normalized(src), dst, src_label=str(src), check=check)


def _prune_stale_files(dst_dir: Path, expected_rels: set[Path], *, check: bool) -> list[str]:
    findings: list[str] = []
    if not dst_dir.is_dir():
        return findings
    for existing in sorted(dst_dir.rglob("*")):
        if existing.is_file() and "__pycache__" not in existing.parts:
            rel = existing.relative_to(dst_dir)
            if rel not in expected_rels:
                if check:
                    findings.append(f"stale: {existing}")
                else:
                    existing.unlink()
                    findings.append(f"removed:{existing}")
    return findings


def discover_platforms(repo_root: Path) -> list[Path]:
    platforms_dir = repo_root / "platforms"
    if not platforms_dir.is_dir():
        return []
    return [p for p in sorted(platforms_dir.iterdir()) if p.is_dir()]


def sync_all(repo_root: Path, check: bool = False) -> list[str]:
    """Sync `core/skills/*`, `core/memory/*`, `core/config/*`, and `core/agents/*` into platform adapters.

    Compiles `core/agents/*.md` using built-in `balanced` defaults (never personal
    `~/.paddock/models.json`) into `platforms/<plat>/agents/`.
    When `check=True`, returns a list of drift/stale messages without modifying disk.
    When `check=False`, updates drifted/missing files and prunes stale files inside
    synced skill and agent directories, returning paths written/removed. Preserves `orchestrating/`.
    """
    core_skills_dir = repo_root / "core" / "skills"
    core_mem_dir = repo_root / "core" / "memory"
    core_cfg_dir = repo_root / "core" / "config"
    core_agents_dir = repo_root / "core" / "agents"
    if not core_skills_dir.is_dir():
        raise FileNotFoundError(f"Missing core/skills directory: {core_skills_dir}")
    if not core_agents_dir.is_dir():
        raise FileNotFoundError(f"Missing core/agents directory: {core_agents_dir}")

    core_skills = [p for p in sorted(core_skills_dir.iterdir()) if p.is_dir()]
    plat_dirs = discover_platforms(repo_root)
    render_targets = [p.name for p in plat_dirs if p.name in agents.SUPPORTED_RENDER_PLATFORMS]
    rendered_by_platform = (
        agents.render_all_platforms(
            core_agents_dir,
            models.default_config(),
            platforms=render_targets,
        )
        if render_targets
        else {}
    )
    changes: list[str] = []

    for plat_dir in plat_dirs:
        plat_skills_dir = plat_dir / "skills"
        for src_skill in core_skills:
            dst_skill = plat_skills_dir / src_skill.name
            expected_rels: set[Path] = set()
            for src_file in sorted(src_skill.rglob("*")):
                if not src_file.is_file() or "__pycache__" in src_file.parts:
                    continue
                rel = src_file.relative_to(src_skill)
                expected_rels.add(rel)
                msg = _sync_file(src_file, dst_skill / rel, check=check)
                if msg:
                    changes.append(msg)

            changes.extend(_prune_stale_files(dst_skill, expected_rels, check=check))

        paddock_core_dst = plat_dir / "scripts" / "paddock_core"
        for src_dir, filenames in (
            (core_mem_dir, CORE_MEMORY_FILES),
            (core_cfg_dir, CORE_CONFIG_FILES),
        ):
            for fname in filenames:
                src_file = src_dir / fname
                if src_file.is_file():
                    msg = _sync_file(src_file, paddock_core_dst / fname, check=check)
                    if msg:
                        changes.append(msg)

        paddock_core_agents_dst = paddock_core_dst / "agents"
        expected_core_agent_rels: set[Path] = set()
        for src_agent in sorted(core_agents_dir.glob("*.md")):
            if not src_agent.is_file():
                continue
            rel = Path(src_agent.name)
            expected_core_agent_rels.add(rel)
            msg = _sync_file(src_agent, paddock_core_agents_dst / rel, check=check)
            if msg:
                changes.append(msg)
        changes.extend(_prune_stale_files(paddock_core_agents_dst, expected_core_agent_rels, check=check))

        if plat_dir.name in rendered_by_platform:
            plat_agents_dst = plat_dir / "agents"
            expected_rendered_rels: set[Path] = set()
            for fname, content in sorted(rendered_by_platform[plat_dir.name].items()):
                rel = Path(fname)
                expected_rendered_rels.add(rel)
                canonical_name = fname.removeprefix("paddock-")
                msg = _sync_bytes(
                    _normalize_lf(content.encode("utf-8")),
                    plat_agents_dst / rel,
                    src_label=f"{core_agents_dir / canonical_name} (balanced)",
                    check=check,
                )
                if msg:
                    changes.append(msg)
            changes.extend(_prune_stale_files(plat_agents_dst, expected_rendered_rels, check=check))

    return changes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sync core skills and memory modules into platform adapters")
    parser.add_argument("--check", action="store_true", help="Exit 1 if any drift exists without writing")
    parser.add_argument("--repo", default=None, help="Repository root override")
    args = parser.parse_args(argv)

    repo_root = (
        Path(args.repo).expanduser().resolve()
        if args.repo
        else Path(__file__).resolve().parent.parent
    )
    findings = sync_all(repo_root, check=args.check)
    if args.check:
        if findings:
            for item in findings:
                print(item, file=sys.stderr)
            return 1
        print("OK: zero sync drift")
        return 0

    for item in findings:
        print(f"synced: {item}")
    if not findings:
        print("OK: already up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
