# /// script
# requires-python = ">=3.12"
# ///
"""Sync platform-agnostic core skills and memory modules into platform adapters without symlinks."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import tempfile

CORE_MEMORY_FILES = ("memory.py", "mcp_server.py")


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


def _sync_file(src: Path, dst: Path, *, check: bool) -> str | None:
    src_bytes = _read_normalized(src)
    if dst.is_file():
        dst_bytes = _read_normalized(dst)
        if dst_bytes == src_bytes:
            return None
    if check:
        return f"drift: {dst} != {src}"
    _atomic_write_bytes(dst, src_bytes)
    return str(dst)


def discover_platforms(repo_root: Path) -> list[Path]:
    platforms_dir = repo_root / "platforms"
    if not platforms_dir.is_dir():
        return []
    return [p for p in sorted(platforms_dir.iterdir()) if p.is_dir()]


def sync_all(repo_root: Path, check: bool = False) -> list[str]:
    """Sync `core/skills/*` and `core/memory/*` into every platform adapter.

    When `check=True`, returns a list of drift messages without modifying disk.
    When `check=False`, updates drifted/missing files and prunes stale files inside
    synced directories, returning paths written/removed. Preserves `orchestrating/`.
    """
    core_skills_dir = repo_root / "core" / "skills"
    core_mem_dir = repo_root / "core" / "memory"
    if not core_skills_dir.is_dir():
        raise FileNotFoundError(f"Missing core/skills directory: {core_skills_dir}")

    core_skills = [p for p in sorted(core_skills_dir.iterdir()) if p.is_dir()]
    changes: list[str] = []

    for plat_dir in discover_platforms(repo_root):
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

            if dst_skill.is_dir():
                for existing in sorted(dst_skill.rglob("*")):
                    if existing.is_file() and "__pycache__" not in existing.parts:
                        rel = existing.relative_to(dst_skill)
                        if rel not in expected_rels:
                            if check:
                                changes.append(f"stale: {existing}")
                            else:
                                existing.unlink()
                                changes.append(f"removed:{existing}")

        paddock_core_dst = plat_dir / "scripts" / "paddock_core"
        for fname in CORE_MEMORY_FILES:
            src_mem = core_mem_dir / fname
            if src_mem.is_file():
                msg = _sync_file(src_mem, paddock_core_dst / fname, check=check)
                if msg:
                    changes.append(msg)

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
