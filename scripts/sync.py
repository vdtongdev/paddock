# /// script
# requires-python = ">=3.12"
# ///
"""Sync platform-agnostic core skills, config modules, and canonical agents into platform adapters."""

from __future__ import annotations

import argparse
import ast
from collections.abc import Callable
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import tomllib

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import agents, models  # noqa: E402

CORE_MEMORY_FILES = ("memory.py", "mcp_server.py")
CORE_CONFIG_FILES = ("models.py", "agents.py")
CORE_GIT_FILES = ("worktree.py", "worktree_mcp_server.py")


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
    """Sync `core/skills/*`, `core/memory/*`, `core/config/*`, `core/git/*`, and `core/agents/*` into platform adapters.

    Compiles `core/agents/*.md` using built-in `balanced` defaults (never personal
    `~/.paddock/models.json`) into `platforms/<plat>/agents/`.
    When `check=True`, returns a list of drift/stale messages without modifying disk.
    When `check=False`, updates drifted/missing files and prunes stale files inside
    synced skill and agent directories, returning paths written/removed. Preserves `orchestrating/`.
    """
    core_skills_dir = repo_root / "core" / "skills"
    core_mem_dir = repo_root / "core" / "memory"
    core_cfg_dir = repo_root / "core" / "config"
    core_git_dir = repo_root / "core" / "git"
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
            (core_git_dir, CORE_GIT_FILES),
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

    if check and _has_package_manifests(repo_root):
        changes.extend(check_version_parity(repo_root))

    return changes


_SEMVER_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?\Z")


def _validate_plain_semver(version: str) -> str | None:
    if "\n" in version or "\r" in version or not _SEMVER_VERSION_RE.match(version):
        return f"invalid semantic version {version!r}"
    if "-" in version:
        _, pre = version.split("-", 1)
        if any(part == "" for part in pre.split(".")):
            return f"invalid semantic version {version!r}"
    return None


def _extract_ast_str_constant(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _read_pyproject_version(path: Path) -> str:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    project = data.get("project")
    if not isinstance(project, dict) or not isinstance(project.get("version"), str):
        raise ValueError("missing [project].version string")
    return project["version"]


def _find_module_target_value(path: Path, target_name: str) -> ast.AST | None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: ast.AST | None = None
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == target_name for t in stmt.targets
        ):
            found = stmt.value
        elif isinstance(stmt, ast.AnnAssign) and (
            isinstance(stmt.target, ast.Name) and stmt.target.id == target_name
        ):
            found = stmt.value
    return found


def _read_init_version(path: Path) -> str:
    found = _extract_ast_str_constant(_find_module_target_value(path, "__version__"))
    if found is None:
        raise ValueError("missing __version__ string literal")
    return found


def _read_json_version(path: Path) -> str:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("version"), str):
        raise ValueError("missing top-level 'version' string")
    return data["version"]


def _read_mcp_server_version(path: Path) -> str:
    value_node = _find_module_target_value(path, "SERVER_INFO")
    found: str | None = None
    if isinstance(value_node, ast.Dict):
        for key_node, val_node in zip(value_node.keys, value_node.values):
            if isinstance(key_node, ast.Constant) and key_node.value == "version":
                found = _extract_ast_str_constant(val_node)
    if found is None:
        raise ValueError('missing SERVER_INFO["version"] string literal')
    return found


_VERSION_PARITY_SOURCES: tuple[tuple[str, Callable[[Path], str]], ...] = (
    ("pyproject.toml", _read_pyproject_version),
    ("paddock/__init__.py", _read_init_version),
    ("platforms/antigravity/plugin.json", _read_json_version),
    ("platforms/claude/.claude-plugin/plugin.json", _read_json_version),
    ("core/memory/mcp_server.py", _read_mcp_server_version),
    ("platforms/antigravity/scripts/paddock_core/mcp_server.py", _read_mcp_server_version),
    ("platforms/claude/scripts/paddock_core/mcp_server.py", _read_mcp_server_version),
    ("core/git/worktree_mcp_server.py", _read_mcp_server_version),
    ("platforms/antigravity/scripts/paddock_core/worktree_mcp_server.py", _read_mcp_server_version),
    ("platforms/claude/scripts/paddock_core/worktree_mcp_server.py", _read_mcp_server_version),
)


def _has_package_manifests(repo_root: Path) -> bool:
    return (repo_root / "pyproject.toml").is_file() or (repo_root / "paddock" / "__init__.py").is_file()


def check_version_parity(repo_root: Path) -> list[str]:
    """Verify one identical semantic version across packaging, plugin manifests, and MCP servers."""
    findings: list[str] = []
    versions: dict[str, str] = {}

    for rel_path, reader in _VERSION_PARITY_SOURCES:
        full_path = repo_root / rel_path
        if not full_path.is_file():
            findings.append(f"version missing: {full_path}")
            continue
        try:
            ver = reader(full_path)
        except (
            OSError,
            UnicodeDecodeError,
            ValueError,
            SyntaxError,
            tomllib.TOMLDecodeError,
        ) as exc:
            findings.append(f"version unparseable: {full_path} ({exc})")
            continue
        err = _validate_plain_semver(ver)
        if err is not None:
            findings.append(f"version invalid: {full_path} ({err})")
            continue
        versions[rel_path] = ver

    if len(set(versions.values())) > 1:
        anchor_rel, anchor_ver = next(iter(versions.items()))
        for rel_path, ver in versions.items():
            if ver != anchor_ver:
                findings.append(
                    f"version drift: {repo_root / rel_path} ({ver}) != {repo_root / anchor_rel} ({anchor_ver})"
                )

    return findings


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
    parity_errors = check_version_parity(repo_root) if _has_package_manifests(repo_root) else []
    if parity_errors:
        for item in parity_errors:
            print(item, file=sys.stderr)
        return 1
    if not findings:
        print("OK: already up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
