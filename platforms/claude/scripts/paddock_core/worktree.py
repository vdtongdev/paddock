# /// script
# requires-python = ">=3.12"
# ///
"""Paddock git worktree lifecycle, containment, and exclude utilities."""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shutil
import subprocess
import tempfile
from typing import Any

VALID_PREFIXES: tuple[str, ...] = ("feat", "fix", "docs", "refactor", "chore", "test")
SLUG_RE: re.Pattern[str] = re.compile(r"\A(?!.*\.\.)[a-z0-9][a-z0-9._-]{0,63}\Z")
BASE_REF_RE: re.Pattern[str] = re.compile(r"\A[A-Za-z0-9_][A-Za-z0-9._/@+-]*\Z")

_BASE_REF_FORBIDDEN_CHARS: frozenset[str] = frozenset({"~", "^", ":", "?", "*", "[", "\\"})
_DEFAULT_TIMEOUT_SECS = 30
_CODEGRAPH_TIMEOUT_SECS = 30


def _has_control_or_newline(text: str) -> bool:
    for ch in text:
        code = ord(ch)
        if code < 0x20 or 0x7F <= code <= 0x9F or code in (0x2028, 0x2029):
            return True
    return False


def _is_symlink_or_junction(path: Path) -> bool:
    """Return True when `path` is a symlink or a Windows directory junction."""
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(callable(is_junction) and is_junction())


def _check_non_symlink_descendants(base: Path, target: Path) -> Path:
    """Verify `base` and every relative segment down to `target` are non-symlink and contained."""
    if _is_symlink_or_junction(base):
        raise ValueError(f"Refusing symlink or junction directory: {base}")
    resolved_base = base.resolve()
    try:
        rel = target.relative_to(base)
    except ValueError:
        try:
            rel = target.relative_to(resolved_base)
            base = resolved_base
        except ValueError as exc:
            raise ValueError(f"Target path {target} escapes base directory {base}") from exc

    cur = base
    for part in rel.parts:
        cur = cur / part
        if _is_symlink_or_junction(cur):
            raise ValueError(f"Refusing symlink or junction path component: {cur}")

    resolved_target = target.resolve()
    if not resolved_target.is_relative_to(resolved_base):
        raise ValueError(f"Resolved target {resolved_target} escapes base {resolved_base}")
    return resolved_target


def validate_prefix(prefix: str) -> str:
    """Validate branch prefix against `VALID_PREFIXES`."""
    if not isinstance(prefix, str) or prefix not in VALID_PREFIXES:
        raise ValueError(f"Invalid branch prefix {prefix!r}; expected one of {VALID_PREFIXES}")
    return prefix


def validate_slug(slug: str) -> str:
    """Validate worktree slug strictly against `SLUG_RE` without path traversal."""
    if not isinstance(slug, str) or not slug:
        raise ValueError("Worktree slug must be a non-empty string")
    if _has_control_or_newline(slug):
        raise ValueError(f"Control character rejected in worktree slug: {slug!r}")
    if ".." in slug:
        raise ValueError(f"Path traversal '..' rejected in worktree slug: {slug!r}")
    if "/" in slug or "\\" in slug:
        raise ValueError(f"Path separator rejected in worktree slug: {slug!r}")
    if not SLUG_RE.fullmatch(slug):
        raise ValueError(
            f"Invalid worktree slug {slug!r}; expected 1-64 chars matching ^[a-z0-9][a-z0-9._-]{{0,63}}$"
        )
    return slug


def validate_base_ref(base: str) -> str:
    """Validate git base ref against leading dashes, control chars, and revision/glob operators."""
    if not isinstance(base, str) or not base:
        raise ValueError("Base ref must be a non-empty string")
    if base.startswith("-"):
        raise ValueError(f"Base ref must not start with '-': {base!r}")
    if _has_control_or_newline(base):
        raise ValueError(f"Control character rejected in base ref: {base!r}")
    if any(ch.isspace() for ch in base):
        raise ValueError(f"Whitespace rejected in base ref: {base!r}")
    if ".." in base or "@{" in base:
        raise ValueError(f"Unsafe revision sequence rejected in base ref: {base!r}")
    if any(ch in _BASE_REF_FORBIDDEN_CHARS for ch in base):
        raise ValueError(f"Forbidden git ref character rejected in base ref: {base!r}")
    if base.startswith("/") or base.endswith("/") or "//" in base:
        raise ValueError(f"Malformed slash in base ref: {base!r}")
    if base == "@" or base.endswith(".lock"):
        raise ValueError(f"Invalid git ref name: {base!r}")
    for part in base.split("/"):
        if not part or part.startswith(".") or part.endswith(".") or part.endswith(".lock"):
            raise ValueError(f"Invalid ref segment {part!r} in base ref: {base!r}")
    if not BASE_REF_RE.fullmatch(base):
        raise ValueError(f"Unsupported characters in base ref: {base!r}")
    return base


def _run_git(cwd: Path, args: list[str], *, timeout: int = _DEFAULT_TIMEOUT_SECS) -> subprocess.CompletedProcess[str]:
    """Execute git with `shell=False` in `cwd`."""
    try:
        return subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            shell=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("git executable not found on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"git command timed out after {timeout}s: {' '.join(args)}") from exc


def _resolve_git_paths(cwd: str | Path) -> tuple[Path, Path]:
    """Resolve `(repo_root, git_common_dir)` cleanly, rejecting non-git, bare, or symlinked paths."""
    if isinstance(cwd, Path):
        raw_str = str(cwd)
    elif isinstance(cwd, str):
        raw_str = cwd
    else:
        raise ValueError(f"cwd must be a string or Path, got {type(cwd).__name__}")

    if not raw_str or not raw_str.strip() or _has_control_or_newline(raw_str):
        raise ValueError(f"Invalid cwd path: {cwd!r}")

    raw_cwd = Path(raw_str).expanduser()
    if _is_symlink_or_junction(raw_cwd):
        raise ValueError(f"Refusing symlink or junction cwd: {raw_cwd}")

    cwd_abs = raw_cwd.absolute()
    if _is_symlink_or_junction(cwd_abs):
        raise ValueError(f"Refusing symlink or junction cwd: {cwd_abs}")

    if not raw_cwd.exists():
        raise FileNotFoundError(f"Directory does not exist: {raw_cwd}")
    if not raw_cwd.is_dir():
        raise ValueError(f"Path is not a directory: {raw_cwd}")

    cwd_resolved = raw_cwd.resolve()

    proc = _run_git(cwd_resolved, ["rev-parse", "--path-format=absolute", "--git-common-dir"])
    common_raw_text = proc.stdout.strip()
    if proc.returncode != 0 or not common_raw_text:
        detail = (proc.stderr or proc.stdout).strip()
        msg = f"Not a git repository: {cwd_resolved}"
        if detail:
            msg = f"{msg} ({detail})"
        raise ValueError(msg)

    raw_common_dir = Path(common_raw_text)
    bare_proc = _run_git(cwd_resolved, ["rev-parse", "--is-bare-repository"])
    if bare_proc.returncode == 0 and bare_proc.stdout.strip().lower() == "true":
        raise ValueError(f"Bare git repositories are not supported: {cwd_resolved}")

    if raw_common_dir.name != ".git":
        raise ValueError(
            f"Bare or non-standard git repository without '.git' common dir is not supported: {raw_common_dir}"
        )

    if _is_symlink_or_junction(raw_common_dir):
        raise ValueError(f"Refusing symlink or junction git common directory: {raw_common_dir}")

    common_dir = raw_common_dir.resolve()
    if _is_symlink_or_junction(common_dir):
        raise ValueError(f"Refusing symlink or junction git common directory: {common_dir}")

    raw_repo_root = raw_common_dir.parent
    if _is_symlink_or_junction(raw_repo_root):
        raise ValueError(f"Refusing symlink or junction repository root: {raw_repo_root}")

    repo_root = common_dir.parent.resolve()
    if _is_symlink_or_junction(repo_root):
        raise ValueError(f"Refusing symlink or junction repository root: {repo_root}")
    if _is_symlink_or_junction(repo_root / ".git"):
        raise ValueError(f"Refusing symlink or junction '.git' directory: {repo_root / '.git'}")

    if cwd_resolved == common_dir or cwd_resolved.is_relative_to(common_dir):
        raise ValueError(f"cwd must not be inside '.git' directory: {cwd_resolved}")

    # Check every path component between cwd and repo_root without traversing OS ancestors above repo_root.
    cur = cwd_abs
    while True:
        if _is_symlink_or_junction(cur):
            raise ValueError(f"Refusing symlink or junction path component: {cur}")
        resolved_cur = cur.resolve()
        if resolved_cur == repo_root or not resolved_cur.is_relative_to(repo_root):
            break
        if cur.parent == cur:
            break
        cur = cur.parent

    return repo_root, common_dir


def resolve_repo_root(cwd: str | Path) -> Path:
    """Resolve the main git checkout root for `cwd` via `--git-common-dir`."""
    repo_root, _ = _resolve_git_paths(cwd)
    return repo_root


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """Atomically write raw bytes to `path` via temp file + `os.replace`."""
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


def _ensure_worktree_exclude_for_common_dir(git_common_dir: Path) -> bool:
    info_dir = git_common_dir / "info"
    exclude_path = info_dir / "exclude"

    if _is_symlink_or_junction(git_common_dir):
        raise ValueError(f"Refusing symlink or junction git common directory: {git_common_dir}")
    if _is_symlink_or_junction(info_dir):
        raise ValueError(f"Refusing symlink or junction info directory: {info_dir}")
    if info_dir.exists() and not info_dir.is_dir():
        raise ValueError(f"Expected directory at {info_dir}, found non-directory")
    if _is_symlink_or_junction(exclude_path):
        raise ValueError(f"Refusing symlink or junction exclude file: {exclude_path}")
    if exclude_path.exists() and not exclude_path.is_file():
        raise ValueError(f"Expected file at {exclude_path}, found non-file")

    _check_non_symlink_descendants(git_common_dir, exclude_path)

    raw_bytes = exclude_path.read_bytes() if exclude_path.exists() else b""
    text = raw_bytes.decode("utf-8", errors="surrogateescape")
    for line in text.splitlines():
        if line.strip() in {".worktrees/", ".worktrees"}:
            return False

    if b"\r\n" in raw_bytes:
        newline = b"\r\n"
    elif b"\n" in raw_bytes:
        newline = b"\n"
    elif b"\r" in raw_bytes:
        newline = b"\r"
    else:
        newline = b"\n"

    entry = b".worktrees/" + newline
    if not raw_bytes:
        new_bytes = entry
    elif raw_bytes.endswith((b"\r\n", b"\n", b"\r")):
        new_bytes = raw_bytes + entry
    else:
        new_bytes = raw_bytes + newline + entry

    info_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write_bytes(exclude_path, new_bytes)
    return True


def ensure_worktree_exclude(repo_root: str | Path) -> bool:
    """Ensure `.worktrees/` exists in `<git_common_dir>/info/exclude` without modifying `.gitignore`."""
    _, git_common_dir = _resolve_git_paths(repo_root)
    return _ensure_worktree_exclude_for_common_dir(git_common_dir)


def _normalize_repo_rel(raw_str: str, *, label: str) -> Path:
    """Parse a relative path string into a clean relative `Path`, rejecting traversal, drives, UNC, and `.git`."""
    if not raw_str or not raw_str.strip() or _has_control_or_newline(raw_str):
        raise ValueError(f"Empty or invalid {label} path: {raw_str!r}")
    if raw_str.startswith(("/", "\\")):
        raise ValueError(f"{label} must be a relative path (leading separator rejected): {raw_str!r}")

    win_path = PureWindowsPath(raw_str)
    posix_path = PurePosixPath(raw_str)
    if win_path.drive or win_path.root or posix_path.root or Path(raw_str).is_absolute():
        raise ValueError(f"{label} must be a relative path (absolute/drive/UNC rejected): {raw_str!r}")

    raw_segments = [seg for seg in re.split(r"[/\\]", raw_str) if seg]
    if ".." in raw_segments or ".." in Path(raw_str).parts:
        raise ValueError(f"Path traversal '..' rejected in {label}: {raw_str!r}")

    clean_segments = [seg for seg in raw_segments if seg != "."]
    if not clean_segments:
        raise ValueError(f"{label} must refer to a file inside the repository: {raw_str!r}")

    rel = Path(*clean_segments)
    if any(part.lower() == ".git" for part in rel.parts):
        raise ValueError(f"Refusing path inside '.git/' for {label}: {raw_str!r}")
    return rel


def _validate_copy_path(repo_root: Path, raw: str | Path) -> Path:
    """Validate one `copy_paths` entry as a relative non-symlink file path inside `repo_root`."""
    if isinstance(raw, Path):
        raw_str = str(raw)
    elif isinstance(raw, str):
        raw_str = raw
    else:
        raise ValueError(f"copy_paths entries must be strings, got {type(raw).__name__}")

    rel = _normalize_repo_rel(raw_str, label="copy_paths")
    src = repo_root / rel
    _check_non_symlink_descendants(repo_root, src)
    if not src.exists():
        raise FileNotFoundError(f"Source file for copy_paths does not exist: {rel.as_posix()}")
    if not src.is_file():
        raise ValueError(f"copy_paths entry must be a regular file: {rel.as_posix()}")
    return rel


def _validate_plan_path(repo_root: Path, plan_path: str | Path) -> Path:
    """Validate `plan_path` (relative or inside `repo_root`) and normalize it to repo-relative `Path`."""
    if isinstance(plan_path, Path):
        raw_str = str(plan_path)
    elif isinstance(plan_path, str):
        raw_str = plan_path
    else:
        raise ValueError(f"plan_path must be a string or Path, got {type(plan_path).__name__}")

    if not raw_str or not raw_str.strip() or _has_control_or_newline(raw_str):
        raise ValueError(f"Empty or invalid plan_path: {plan_path!r}")

    raw_segments = [seg for seg in re.split(r"[/\\]", raw_str) if seg]
    if ".." in raw_segments or ".." in Path(raw_str).parts:
        raise ValueError(f"Path traversal '..' rejected in plan_path: {plan_path!r}")

    win_path = PureWindowsPath(raw_str)
    p = Path(raw_str)

    # Reject drive-relative (`C:foo`) or platform-mismatch absolute paths (`C:\foo` on POSIX, `/foo` on Windows).
    if win_path.drive and not win_path.root:
        raise ValueError(f"Drive-relative plan_path rejected: {plan_path!r}")
    if (win_path.drive or raw_str.startswith("\\\\")) and not p.is_absolute():
        raise ValueError(f"Path escapes repository root: {plan_path!r}")
    if raw_str.startswith(("/", "\\")) and not p.is_absolute():
        raise ValueError(f"Rooted plan_path without drive rejected: {plan_path!r}")

    if p.is_absolute():
        if _is_symlink_or_junction(p):
            raise ValueError(f"Refusing symlink or junction plan_path: {p}")
        rel: Path | None = None
        if p.is_relative_to(repo_root):
            rel = p.relative_to(repo_root)
        else:
            cur = p
            while True:
                if _is_symlink_or_junction(cur):
                    raise ValueError(f"Refusing symlink or junction path component in plan_path: {cur}")
                resolved_cur = cur.resolve()
                if resolved_cur == repo_root or not resolved_cur.is_relative_to(repo_root):
                    break
                if cur.parent == cur:
                    break
                cur = cur.parent
            resolved_p = p.resolve()
            if resolved_p.is_relative_to(repo_root):
                rel = resolved_p.relative_to(repo_root)
        if rel is None or not rel.parts:
            raise ValueError(f"plan_path must be strictly inside repository root {repo_root}: {plan_path!r}")
        if any(part.lower() == ".git" for part in rel.parts):
            raise ValueError(f"Refusing plan_path inside '.git/': {plan_path!r}")
        target = repo_root / rel
        _check_non_symlink_descendants(repo_root, target)
    else:
        rel = _normalize_repo_rel(raw_str, label="plan_path")
        target = repo_root / rel
        _check_non_symlink_descendants(repo_root, target)

    if target.exists() and not target.is_file():
        raise ValueError(f"plan_path must point to a regular file: {rel.as_posix()}")
    return rel


def _list_worktrees_for_root(repo_root: Path) -> list[dict[str, Any]]:
    proc = _run_git(repo_root, ["worktree", "list", "--porcelain"])
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise RuntimeError(f"git worktree list failed: {detail}")

    managed_dir = (repo_root / ".worktrees").resolve()
    managed_dir_is_link = _is_symlink_or_junction(repo_root / ".worktrees")

    entries: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    def _finalize(item: dict[str, Any]) -> dict[str, Any]:
        entry_path = Path(item["path"]).resolve()
        is_main = entry_path == repo_root
        name = entry_path.name
        is_paddock_managed = (
            not is_main
            and not managed_dir_is_link
            and entry_path.parent == managed_dir
            and ".." not in name
            and bool(SLUG_RE.fullmatch(name))
        )
        return {
            "path": str(entry_path),
            "head": item.get("head"),
            "branch": item.get("branch"),
            "bare": bool(item.get("bare", False)),
            "detached": bool(item.get("detached", False)),
            "locked": bool(item.get("locked", False)),
            "prunable": bool(item.get("prunable", False)),
            "is_main": is_main,
            "is_paddock_managed": is_paddock_managed,
            "slug": name if is_paddock_managed else None,
        }

    for raw_line in proc.stdout.splitlines():
        line = raw_line.rstrip("\r\n")
        if not line:
            if current is not None:
                entries.append(_finalize(current))
                current = None
            continue
        if line.startswith("worktree "):
            if current is not None:
                entries.append(_finalize(current))
            current = {"path": line.removeprefix("worktree ")}
            continue
        if current is None:
            continue
        if line.startswith("HEAD "):
            current["head"] = line.removeprefix("HEAD ")
        elif line.startswith("branch "):
            raw_branch = line.removeprefix("branch ")
            current["branch"] = raw_branch.removeprefix("refs/heads/")
        elif line == "bare":
            current["bare"] = True
        elif line == "detached":
            current["detached"] = True
        elif line == "locked" or line.startswith("locked "):
            current["locked"] = True
        elif line == "prunable" or line.startswith("prunable "):
            current["prunable"] = True

    if current is not None:
        entries.append(_finalize(current))

    return entries


def list_worktrees(cwd: str | Path) -> dict[str, Any]:
    """List all registered git worktrees for the repository containing `cwd`."""
    repo_root, _ = _resolve_git_paths(cwd)
    return {
        "repo_root": str(repo_root),
        "worktrees": _list_worktrees_for_root(repo_root),
    }


def _ref_exists(repo_root: Path, full_ref: str) -> bool:
    proc = _run_git(repo_root, ["show-ref", "--verify", "--quiet", "--", full_ref])
    return proc.returncode == 0


def _detect_default_base(repo_root: Path) -> str:
    if _ref_exists(repo_root, "refs/heads/main"):
        return "main"
    if _ref_exists(repo_root, "refs/heads/master"):
        return "master"
    return "HEAD"


def _is_tracked_or_staged(git_dir_cwd: Path, rel_posix: str) -> bool:
    ls_proc = _run_git(git_dir_cwd, ["ls-files", "--error-unmatch", "--", rel_posix])
    if ls_proc.returncode == 0:
        return True
    st_proc = _run_git(git_dir_cwd, ["status", "--porcelain=v1", "--untracked-files=no", "--", rel_posix])
    return st_proc.returncode == 0 and bool(st_proc.stdout.strip())


def _discover_untracked_slug_docs(repo_root: Path, slug: str) -> list[Path]:
    discovered: list[Path] = []
    docs_dir = repo_root / "docs"
    if _is_symlink_or_junction(docs_dir):
        raise ValueError(f"Refusing symlink or junction directory: {docs_dir}")

    for sub in ("specs", "plans"):
        sub_dir = docs_dir / sub
        if _is_symlink_or_junction(sub_dir):
            raise ValueError(f"Refusing symlink or junction directory: {sub_dir}")
        if not sub_dir.is_dir():
            continue
        for cand in sorted(sub_dir.glob(f"{slug}*.md")):
            if _is_symlink_or_junction(cand):
                raise ValueError(f"Refusing symlink or junction document: {cand}")
            if not cand.is_file():
                continue
            rel = cand.relative_to(repo_root)
            _check_non_symlink_descendants(repo_root, cand)
            if not _is_tracked_or_staged(repo_root, rel.as_posix()):
                discovered.append(rel)
    return discovered


def _copy_doc_to_worktree(
    repo_root: Path,
    worktree_path: Path,
    rel: Path,
    *,
    explicit: bool,
) -> bool:
    rel_posix = rel.as_posix()
    src = repo_root / rel
    dst = worktree_path / rel

    _check_non_symlink_descendants(repo_root, src)
    if not src.exists() or not src.is_file():
        return False

    if explicit:
        # Explicit copy_paths / plan_path only copy when untracked in repo_root or missing in worktree.
        if _is_tracked_or_staged(repo_root, rel_posix) and dst.exists():
            return False

    _check_non_symlink_descendants(worktree_path, dst)
    if dst.exists():
        if not dst.is_file():
            raise ValueError(f"Destination path in worktree is not a regular file: {dst}")
        if _is_tracked_or_staged(worktree_path, rel_posix):
            return False
        if src.read_bytes() == dst.read_bytes():
            return False
        if dst.stat().st_mtime >= src.stat().st_mtime:
            return False
    elif _is_tracked_or_staged(worktree_path, rel_posix):
        return False

    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return True


def _maybe_init_codegraph(
    repo_root: Path,
    worktree_path: Path,
    *,
    init_codegraph: bool,
) -> str:
    if not init_codegraph:
        return "skipped_disabled"

    src_index = repo_root / ".codegraph"
    if _is_symlink_or_junction(src_index) or not src_index.is_dir():
        return "skipped_no_source_index"

    dst_index = worktree_path / ".codegraph"
    if _is_symlink_or_junction(dst_index):
        return "failed: refusing symlink or junction .codegraph directory"
    if dst_index.exists():
        return "already_initialized"

    codegraph_bin = shutil.which("codegraph")
    if not codegraph_bin:
        return "skipped_missing_cli"

    try:
        proc = subprocess.run(
            [codegraph_bin, "init", "-y"],
            cwd=str(worktree_path),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_CODEGRAPH_TIMEOUT_SECS,
            shell=False,
        )
    except subprocess.TimeoutExpired:
        return f"failed: timed out after {_CODEGRAPH_TIMEOUT_SECS}s"
    except OSError as exc:
        return f"failed: {exc}"

    if proc.returncode == 0:
        return "initialized"

    detail = (proc.stderr or proc.stdout or f"exit code {proc.returncode}").strip()
    first_line = detail.splitlines()[0] if detail else f"exit code {proc.returncode}"
    return f"failed: {first_line[:200]}"


def create_worktree(
    cwd: str | Path,
    slug: str,
    *,
    prefix: str = "feat",
    base: str | None = None,
    plan_path: str | Path | None = None,
    copy_paths: list[str] | tuple[str, ...] | None = None,
    init_codegraph: bool = True,
) -> dict[str, Any]:
    """Idempotently create or reuse `.worktrees/<slug>` on `<prefix>/<slug>`."""
    clean_slug = validate_slug(slug)
    clean_prefix = validate_prefix(prefix)
    if not isinstance(init_codegraph, bool):
        raise ValueError("init_codegraph must be a boolean")
    if base is not None:
        validate_base_ref(base)

    repo_root, git_common_dir = _resolve_git_paths(cwd)
    target_branch = f"{clean_prefix}/{clean_slug}"

    worktrees_dir = repo_root / ".worktrees"
    if _is_symlink_or_junction(worktrees_dir):
        raise ValueError(f"Refusing symlink or junction worktrees directory: {worktrees_dir}")
    if worktrees_dir.exists() and not worktrees_dir.is_dir():
        raise ValueError(f"Expected directory at {worktrees_dir}, found non-directory")

    target_wt = worktrees_dir / clean_slug
    resolved_target_wt = _check_non_symlink_descendants(repo_root, target_wt)
    if resolved_target_wt.parent != worktrees_dir.resolve():
        raise ValueError(f"Worktree path escapes '.worktrees': {resolved_target_wt}")

    validated_plan_rel: Path | None = None
    if plan_path is not None:
        validated_plan_rel = _validate_plan_path(repo_root, plan_path)

    validated_copy_rels: list[Path] = []
    if copy_paths is not None:
        if isinstance(copy_paths, (str, bytes, Path)) or not isinstance(copy_paths, (list, tuple)):
            raise ValueError("copy_paths must be a list or tuple of relative paths")
        for item in copy_paths:
            validated_copy_rels.append(_validate_copy_path(repo_root, item))

    discovered_docs = _discover_untracked_slug_docs(repo_root, clean_slug)

    resolved_base = base if base is not None else _detect_default_base(repo_root)
    existing_worktrees = _list_worktrees_for_root(repo_root)
    by_path = {Path(w["path"]): w for w in existing_worktrees}
    branch_owner = next((w for w in existing_worktrees if w["branch"] == target_branch), None)

    created = False
    reused = False
    reused_branch = False

    if resolved_target_wt in by_path:
        entry = by_path[resolved_target_wt]
        if entry["prunable"] or not resolved_target_wt.exists():
            raise ValueError(
                f"Registered worktree '{resolved_target_wt}' is missing on disk or prunable; clean it up before recreating"
            )
        if entry["detached"]:
            raise ValueError(
                f"Worktree '{resolved_target_wt}' already exists in detached HEAD state (expected branch '{target_branch}')"
            )
        if entry["branch"] != target_branch:
            raise ValueError(
                f"Worktree '{resolved_target_wt}' already exists on branch '{entry['branch']}' (expected '{target_branch}')"
            )
        reused = True
        exclude_updated = _ensure_worktree_exclude_for_common_dir(git_common_dir)
    elif resolved_target_wt.exists():
        raise ValueError(
            f"Path '{resolved_target_wt}' already exists and is not a registered git worktree"
        )
    elif _ref_exists(repo_root, f"refs/heads/{target_branch}"):
        if branch_owner is not None:
            raise ValueError(
                f"Branch '{target_branch}' is already checked out in worktree '{branch_owner['path']}'"
            )
        exclude_updated = _ensure_worktree_exclude_for_common_dir(git_common_dir)
        worktrees_dir.mkdir(parents=True, exist_ok=True)
        proc = _run_git(
            repo_root,
            ["worktree", "add", "--", str(resolved_target_wt), target_branch],
        )
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout).strip()
            raise RuntimeError(
                f"Failed to attach branch '{target_branch}' at '{resolved_target_wt}': {detail}"
            )
        created = True
        reused_branch = True
    else:
        exclude_updated = _ensure_worktree_exclude_for_common_dir(git_common_dir)
        worktrees_dir.mkdir(parents=True, exist_ok=True)
        proc = _run_git(
            repo_root,
            ["worktree", "add", "-b", target_branch, "--", str(resolved_target_wt), resolved_base],
        )
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout).strip()
            raise RuntimeError(
                f"Failed to create worktree '{resolved_target_wt}' on '{target_branch}' from '{resolved_base}': {detail}"
            )
        created = True

    candidates: dict[Path, bool] = {}
    for rel in discovered_docs:
        candidates.setdefault(rel, False)
    if validated_plan_rel is not None:
        candidates[validated_plan_rel] = True
    for rel in validated_copy_rels:
        candidates[rel] = True

    copied_files: list[str] = []
    for rel, explicit in candidates.items():
        if _copy_doc_to_worktree(repo_root, resolved_target_wt, rel, explicit=explicit):
            copied_files.append(rel.as_posix())

    codegraph_status = _maybe_init_codegraph(
        repo_root,
        resolved_target_wt,
        init_codegraph=init_codegraph,
    )

    return {
        "repo_root": str(repo_root),
        "worktree_path": str(resolved_target_wt),
        "branch": target_branch,
        "base": resolved_base,
        "created": created,
        "reused": reused,
        "reused_branch": reused_branch,
        "exclude_updated": exclude_updated,
        "copied_files": copied_files,
        "codegraph_status": codegraph_status,
    }


def remove_worktree(
    cwd: str | Path,
    slug: str,
    *,
    delete_branch: bool = False,
) -> dict[str, Any]:
    """Strictly non-force remove registered `.worktrees/<slug>`, optionally deleting merged branch via `-d`."""
    clean_slug = validate_slug(slug)
    if not isinstance(delete_branch, bool):
        raise ValueError("delete_branch must be a boolean")

    repo_root, _ = _resolve_git_paths(cwd)
    target_wt = repo_root / ".worktrees" / clean_slug
    resolved_target_wt = _check_non_symlink_descendants(repo_root, target_wt)

    existing_worktrees = _list_worktrees_for_root(repo_root)
    entry = next(
        (w for w in existing_worktrees if Path(w["path"]) == resolved_target_wt),
        None,
    )
    if entry is None:
        raise ValueError(f"Worktree '{resolved_target_wt}' is not a registered git worktree")
    if entry["is_main"] or not entry["is_paddock_managed"] or entry["slug"] != clean_slug:
        raise ValueError(
            f"Refusing to remove non-Paddock or main worktree: '{resolved_target_wt}'"
        )

    branch = entry["branch"]
    rm_proc = _run_git(repo_root, ["worktree", "remove", "--", str(resolved_target_wt)])
    if rm_proc.returncode != 0:
        detail = (rm_proc.stderr or rm_proc.stdout).strip()
        raise RuntimeError(f"Failed to remove worktree '{resolved_target_wt}': {detail}")

    branch_deleted = False
    branch_delete_warning: str | None = None

    if delete_branch:
        if branch is None:
            branch_delete_warning = "Worktree had detached HEAD; no branch to delete."
        elif branch in ("main", "master"):
            branch_delete_warning = f"Refusing to delete protected branch '{branch}'."
        else:
            del_proc = _run_git(repo_root, ["branch", "-d", "--", branch])
            if del_proc.returncode == 0:
                branch_deleted = True
            else:
                detail = (del_proc.stderr or del_proc.stdout or f"git branch -d exited with {del_proc.returncode}").strip()
                branch_delete_warning = detail

    return {
        "repo_root": str(repo_root),
        "worktree_path": str(resolved_target_wt),
        "slug": clean_slug,
        "removed": True,
        "branch": branch,
        "branch_deleted": branch_deleted,
        "branch_delete_warning": branch_delete_warning,
    }
