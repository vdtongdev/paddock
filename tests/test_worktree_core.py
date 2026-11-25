"""Unit and integration tests for core/git/worktree.py."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from core.git import worktree


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _init_repo(path: Path, *, branch: str = "main") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-b", branch)
    _git(path, "config", "user.name", "Test User")
    _git(path, "config", "user.email", "test@example.com")
    (path / "README.md").write_text("# Test Repo\n", encoding="utf-8")
    _git(path, "add", "README.md")
    _git(path, "commit", "-m", "initial commit")
    return path


_ORIG_IS_SYMLINK = Path.is_symlink
_ORIG_IS_JUNCTION = getattr(Path, "is_junction", None)


def _patch_symlink_or_junction(
    monkeypatch: pytest.MonkeyPatch,
    *,
    symlinks: set[Path] | None = None,
    junctions: set[Path] | None = None,
) -> None:
    sym_targets = symlinks or set()
    junc_targets = junctions or set()

    def fake_is_symlink(self: Path) -> bool:
        if self in sym_targets:
            return True
        return _ORIG_IS_SYMLINK(self)

    def fake_is_junction(self: Path) -> bool:
        if self in junc_targets:
            return True
        if callable(_ORIG_IS_JUNCTION):
            return bool(_ORIG_IS_JUNCTION(self))
        return False

    monkeypatch.setattr(Path, "is_symlink", fake_is_symlink)
    monkeypatch.setattr(Path, "is_junction", fake_is_junction, raising=False)


def test_slug_prefix_and_base_ref_validation() -> None:
    for good_slug in ("a", "0", "worktree-mcp", "feat_1.2-rc1", "a" * 64):
        assert worktree.validate_slug(good_slug) == good_slug
        assert worktree.SLUG_RE.fullmatch(good_slug) is not None

    bad_slugs: list[object] = [
        "",
        "a" * 65,
        "foo\n",
        "foo\r",
        "foo\x00bar",
        "foo..bar",
        "..",
        "a..b",
        "foo/bar",
        "foo\\bar",
        "-leading",
        ".leading",
        "_leading",
        "Upper",
        "café",
        "has space",
        None,
        123,
    ]
    for bad in bad_slugs:
        with pytest.raises(ValueError):
            worktree.validate_slug(bad)  # type: ignore[arg-type]
    assert worktree.SLUG_RE.fullmatch("foo\n") is None
    assert worktree.SLUG_RE.fullmatch("foo..bar") is None

    assert worktree.VALID_PREFIXES == ("feat", "fix", "docs", "refactor", "chore", "test")
    for good_prefix in worktree.VALID_PREFIXES:
        assert worktree.validate_prefix(good_prefix) == good_prefix

    for bad_prefix in ("", "feature", "FEAT", "-feat", "feat/sub", None, 42):
        with pytest.raises(ValueError):
            worktree.validate_prefix(bad_prefix)  # type: ignore[arg-type]

    for good_ref in (
        "HEAD",
        "main",
        "master",
        "origin/main",
        "refs/heads/feat/v1.0",
        "v1.2.3+build1",
        "abc123def",
    ):
        assert worktree.validate_base_ref(good_ref) == good_ref

    bad_refs: list[object] = [
        "",
        "-main",
        "--branch",
        "main\n",
        "main\r",
        "main\x00",
        "main branch",
        "main\t1",
        "a..b",
        "main@{0}",
        "main~1",
        "main^2",
        "main:file",
        "feat/*",
        "feat?",
        "feat[0]",
        "feat\\bar",
        "main.lock",
        "refs/heads/foo.lock/bar",
        "/main",
        "main/",
        "refs//main",
        ".hidden",
        "main.",
        "@",
        "café",
        None,
    ]
    for bad_ref in bad_refs:
        with pytest.raises(ValueError):
            worktree.validate_base_ref(bad_ref)  # type: ignore[arg-type]


def test_resolve_repo_root_and_errors(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    sub = repo / "sub" / "nested"
    sub.mkdir(parents=True)

    assert worktree.resolve_repo_root(repo) == repo.resolve()
    assert worktree.resolve_repo_root(sub) == repo.resolve()
    root, common = worktree._resolve_git_paths(sub)
    assert root == repo.resolve()
    assert common == (repo / ".git").resolve()

    # Non-existent path
    with pytest.raises(FileNotFoundError):
        worktree.resolve_repo_root(tmp_path / "missing")

    # Regular file instead of directory
    with pytest.raises(ValueError, match="not a directory"):
        worktree.resolve_repo_root(repo / "README.md")

    # Non-git directory
    non_git = tmp_path / "plain"
    non_git.mkdir()
    with pytest.raises(ValueError, match="Not a git repository"):
        worktree.resolve_repo_root(non_git)

    # Inside .git directory
    with pytest.raises(ValueError, match="\\.git"):
        worktree.resolve_repo_root(repo / ".git")

    # Bare repository (standard name and .git name)
    bare1 = tmp_path / "bare.git"
    subprocess.run(["git", "init", "--bare", str(bare1)], check=True, capture_output=True)
    with pytest.raises(ValueError, match="Bare"):
        worktree.resolve_repo_root(bare1)

    bare2 = tmp_path / "weird" / ".git"
    bare2.parent.mkdir(parents=True)
    subprocess.run(["git", "init", "--bare", str(bare2)], check=True, capture_output=True)
    with pytest.raises(ValueError, match="Bare"):
        worktree.resolve_repo_root(bare2)


def test_symlink_junction_and_containment_rejections(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = _init_repo(tmp_path / "repo")
    resolved_repo = repo.resolve()
    (repo / "docs" / "plans").mkdir(parents=True)
    plan_file = repo / "docs" / "plans" / "my-feat.md"
    plan_file.write_text("# Plan\n", encoding="utf-8")
    extra_file = repo / "notes.txt"
    extra_file.write_text("note\n", encoding="utf-8")
    outside_file = tmp_path / "secret.md"
    outside_file.write_text("secret\n", encoding="utf-8")

    # Symlink / junction rejections across repo_root, .worktrees, .git/info/exclude, docs, copy_paths, plan_path
    _patch_symlink_or_junction(monkeypatch, symlinks={repo})
    with pytest.raises(ValueError, match="symlink or junction"):
        worktree.resolve_repo_root(repo)

    # Symlink/junction on ancestor between cwd subdir and repo_root
    sub_dir = repo / "sub"
    sub_dir.mkdir()
    _patch_symlink_or_junction(monkeypatch, junctions={repo})
    with pytest.raises(ValueError, match="symlink or junction"):
        worktree.resolve_repo_root(sub_dir)

    _patch_symlink_or_junction(monkeypatch, junctions={resolved_repo / ".worktrees"})
    with pytest.raises(ValueError, match="symlink or junction"):
        worktree.create_worktree(repo, "my-feat", init_codegraph=False)

    _patch_symlink_or_junction(monkeypatch, symlinks={resolved_repo / ".worktrees" / "my-feat"})
    with pytest.raises(ValueError, match="symlink or junction"):
        worktree.create_worktree(repo, "my-feat", init_codegraph=False)

    _patch_symlink_or_junction(monkeypatch, junctions={resolved_repo / ".git" / "info"})
    with pytest.raises(ValueError, match="symlink or junction"):
        worktree.ensure_worktree_exclude(repo)

    _patch_symlink_or_junction(monkeypatch, symlinks={resolved_repo / ".git" / "info" / "exclude"})
    with pytest.raises(ValueError, match="symlink or junction"):
        worktree.ensure_worktree_exclude(repo)

    _patch_symlink_or_junction(monkeypatch, junctions={resolved_repo / "notes.txt"})
    with pytest.raises(ValueError, match="symlink or junction"):
        worktree.create_worktree(repo, "my-feat", copy_paths=["notes.txt"], init_codegraph=False)

    _patch_symlink_or_junction(monkeypatch, symlinks={resolved_repo / "docs" / "plans"})
    with pytest.raises(ValueError, match="symlink or junction"):
        worktree.create_worktree(repo, "my-feat", plan_path="docs/plans/my-feat.md", init_codegraph=False)

    _patch_symlink_or_junction(monkeypatch, symlinks={resolved_repo / "docs" / "plans" / "my-feat.md"})
    with pytest.raises(ValueError, match="symlink or junction"):
        worktree.create_worktree(repo, "my-feat", init_codegraph=False)

    _patch_symlink_or_junction(monkeypatch)  # reset

    # copy_paths traversal, absolute, drive, UNC, .git/, directory, and missing rejections
    bad_copy_paths = [
        "../secret.md",
        "docs/../../secret.md",
        "/etc/passwd",
        r"\Windows\System32\hosts",
        r"\\server\share\file.txt",
        "//server/share/file.txt",
        r"C:\temp\file.txt",
        "C:/temp/file.txt",
        "C:notes.txt",
        ".git/config",
        ".git/hooks/pre-commit",
        ".GIT/HEAD",
        "sub/.git/config",
        ".",
        "./",
        "",
        "notes\n.txt",
    ]
    for bad_cp in bad_copy_paths:
        with pytest.raises(ValueError, match="copy_paths"):
            worktree.create_worktree(repo, "my-feat", copy_paths=[bad_cp], init_codegraph=False)

    with pytest.raises(ValueError, match="list or tuple"):
        worktree.create_worktree(
            repo,
            "my-feat",
            copy_paths="notes.txt",  # type: ignore[arg-type]
            init_codegraph=False,
        )
    with pytest.raises(ValueError, match="regular file"):
        worktree.create_worktree(repo, "my-feat", copy_paths=["docs/plans"], init_codegraph=False)
    with pytest.raises(FileNotFoundError):
        worktree.create_worktree(repo, "my-feat", copy_paths=["missing.txt"], init_codegraph=False)

    # plan_path rejections and valid normalization
    bad_plan_paths: list[object] = [
        "",
        "../secret.md",
        "docs/../secret.md",
        str(outside_file),
        str(repo),
        ".git/plan.md",
        str(repo / ".git" / "plan.md"),
        "C:docs/plans/my-feat.md",
        "/etc/passwd",
        r"\Windows\plan.md",
        r"\\server\share\plan.md",
        "docs/plans",
        123,
    ]
    for bad_pp in bad_plan_paths:
        with pytest.raises(ValueError):
            worktree.create_worktree(
                repo,
                "my-feat",
                plan_path=bad_pp,  # type: ignore[arg-type]
                init_codegraph=False,
            )

    # Confirm no partial worktree was created on any failed validation above
    assert not (repo / ".worktrees" / "my-feat").exists()

    # Valid absolute plan_path strictly inside repo_root normalizes to repo-relative and copies
    res = worktree.create_worktree(
        repo,
        "my-feat",
        plan_path=str(plan_file),
        init_codegraph=False,
    )
    assert res["copied_files"] == ["docs/plans/my-feat.md"]
    assert (Path(res["worktree_path"]) / "docs" / "plans" / "my-feat.md").read_text(encoding="utf-8") == "# Plan\n"

    # Destination symlink inside worktree refuses copying into escaped directory
    _patch_symlink_or_junction(
        monkeypatch,
        junctions={Path(res["worktree_path"]) / "docs"},
    )
    with pytest.raises(ValueError, match="symlink or junction"):
        worktree.create_worktree(repo, "my-feat", copy_paths=["docs/plans/my-feat.md"], init_codegraph=False)
    _patch_symlink_or_junction(monkeypatch)


def test_ensure_worktree_exclude_idempotency_and_line_endings(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    info_dir = repo / ".git" / "info"
    exclude = info_dir / "exclude"
    if exclude.exists():
        exclude.unlink()
    if info_dir.exists():
        info_dir.rmdir()

    # Creates info/ if absent
    assert worktree.ensure_worktree_exclude(repo) is True
    assert exclude.read_bytes() == b".worktrees/\n"
    assert worktree.ensure_worktree_exclude(repo) is False

    # Preserves CRLF line endings and existing comments (including `# .worktrees/`)
    exclude.write_bytes(b"# comment\r\n# .worktrees/\r\n*.log\r\n")
    assert worktree.ensure_worktree_exclude(repo) is True
    assert exclude.read_bytes() == b"# comment\r\n# .worktrees/\r\n*.log\r\n.worktrees/\r\n"
    assert worktree.ensure_worktree_exclude(repo) is False

    # Recognizes `.worktrees` without trailing slash
    exclude.write_bytes(b"*.tmp\r\n.worktrees\r\n")
    assert worktree.ensure_worktree_exclude(repo) is False
    assert exclude.read_bytes() == b"*.tmp\r\n.worktrees\r\n"

    # Preserves file without trailing newline
    exclude.write_bytes(b"*.tmp")
    assert worktree.ensure_worktree_exclude(repo) is True
    assert exclude.read_bytes() == b"*.tmp\n.worktrees/\n"

    assert not (repo / ".gitignore").exists()


def test_create_reuse_branch_attach_and_conflicts(tmp_path: Path) -> None:
    # Auto-detect master when main does not exist
    master_repo = _init_repo(tmp_path / "master-repo", branch="master")
    res_master = worktree.create_worktree(master_repo, "m-slug", init_codegraph=False)
    assert res_master["base"] == "master"
    assert res_master["branch"] == "feat/m-slug"
    assert res_master["created"] is True
    assert res_master["reused"] is False
    assert res_master["reused_branch"] is False

    # Auto-detect HEAD when neither main nor master exists
    trunk_repo = _init_repo(tmp_path / "trunk-repo", branch="trunk")
    res_trunk = worktree.create_worktree(trunk_repo, "t-slug", prefix="fix", init_codegraph=False)
    assert res_trunk["base"] == "HEAD"
    assert res_trunk["branch"] == "fix/t-slug"

    # Main repo tests: create -> idempotent reuse -> branch attach -> conflicts
    repo = _init_repo(tmp_path / "repo", branch="main")
    created = worktree.create_worktree(repo, "alpha", init_codegraph=False)
    assert json.loads(json.dumps(created)) == created
    assert set(created.keys()) == {
        "repo_root",
        "worktree_path",
        "branch",
        "base",
        "created",
        "reused",
        "reused_branch",
        "exclude_updated",
        "copied_files",
        "codegraph_status",
    }
    assert created["base"] == "main"
    assert created["branch"] == "feat/alpha"
    assert created["created"] is True
    assert created["reused"] is False
    assert created["reused_branch"] is False
    assert created["exclude_updated"] is True
    assert created["codegraph_status"] == "skipped_disabled"

    # Idempotent reuse (works even when invoked with cwd inside the worktree)
    wt_path = Path(created["worktree_path"])
    reused = worktree.create_worktree(wt_path, "alpha", init_codegraph=False)
    assert reused["created"] is False
    assert reused["reused"] is True
    assert reused["reused_branch"] is False
    assert reused["exclude_updated"] is False
    assert reused["copied_files"] == []

    # Conflict: same slug with different prefix when .worktrees/alpha is on feat/alpha
    with pytest.raises(ValueError, match="already exists on branch 'feat/alpha'"):
        worktree.create_worktree(repo, "alpha", prefix="fix", init_codegraph=False)

    # Conflict: detached worktree at .worktrees/detached-wt
    detached_wt = repo / ".worktrees" / "detached-wt"
    _git(repo, "worktree", "add", "--detach", str(detached_wt), "main")
    with pytest.raises(ValueError, match="detached HEAD"):
        worktree.create_worktree(repo, "detached-wt", init_codegraph=False)

    # Conflict: untracked non-worktree directory at .worktrees/occupied
    occupied = repo / ".worktrees" / "occupied"
    occupied.mkdir(parents=True)
    (occupied / "keep.txt").write_text("important\n", encoding="utf-8")
    with pytest.raises(ValueError, match="not a registered git worktree"):
        worktree.create_worktree(repo, "occupied", init_codegraph=False)
    assert (occupied / "keep.txt").read_text(encoding="utf-8") == "important\n"

    # Attach existing unattached local branch
    _git(repo, "branch", "chore/existing-br", "main")
    attached = worktree.create_worktree(repo, "existing-br", prefix="chore", init_codegraph=False)
    assert attached["created"] is True
    assert attached["reused"] is False
    assert attached["reused_branch"] is True
    assert attached["branch"] == "chore/existing-br"

    # Conflict: branch already checked out in external worktree names the owning worktree
    ext_wt = tmp_path / "external-wt"
    _git(repo, "worktree", "add", "-b", "feat/owned-elsewhere", str(ext_wt), "main")
    with pytest.raises(ValueError, match="external-wt"):
        worktree.create_worktree(repo, "owned-elsewhere", init_codegraph=False)


def test_copy_docs_mtime_preservation_and_overwrite_guards(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")

    # Commit one tracked spec file and one tracked file on main before branching
    (repo / "docs" / "specs").mkdir(parents=True)
    (repo / "docs" / "plans").mkdir(parents=True)
    tracked_spec = repo / "docs" / "specs" / "demo-tracked.md"
    tracked_spec.write_text("tracked spec\n", encoding="utf-8")
    _git(repo, "add", "docs/specs/demo-tracked.md")
    _git(repo, "commit", "-m", "add tracked spec")
    base_commit = _git(repo, "rev-parse", "HEAD~1").stdout.strip()

    # Untracked spec, plan, and extra file with explicit older mtime
    untracked_spec = repo / "docs" / "specs" / "demo.md"
    untracked_spec.write_text("spec v1\n", encoding="utf-8")
    untracked_plan = repo / "docs" / "plans" / "demo.md"
    untracked_plan.write_text("plan v1\n", encoding="utf-8")
    extra_doc = repo / "docs" / "extra.txt"
    extra_doc.write_text("extra v1\n", encoding="utf-8")
    old_ts = 1_700_000_000.0
    os.utime(untracked_plan, (old_ts, old_ts))

    res = worktree.create_worktree(
        repo,
        "demo",
        base=base_commit,
        plan_path="docs/plans/demo.md",
        copy_paths=["docs/extra.txt", "docs/specs/demo-tracked.md"],
        init_codegraph=False,
    )
    wt = Path(res["worktree_path"])
    assert res["copied_files"] == [
        "docs/specs/demo.md",
        "docs/plans/demo.md",
        "docs/extra.txt",
        "docs/specs/demo-tracked.md",
    ]
    wt_plan = wt / "docs" / "plans" / "demo.md"
    assert abs(wt_plan.stat().st_mtime - old_ts) < 0.01

    # Idempotent second run skips identical files
    res2 = worktree.create_worktree(
        repo,
        "demo",
        plan_path="docs/plans/demo.md",
        copy_paths=["docs/extra.txt"],
        init_codegraph=False,
    )
    assert res2["copied_files"] == []

    # Newer untracked edit in worktree must NOT be overwritten even if source changed
    wt_plan.write_text("edited in worktree\n", encoding="utf-8")
    wt_newer_ts = old_ts + 200.0
    os.utime(wt_plan, (wt_newer_ts, wt_newer_ts))
    untracked_plan.write_text("source v2\n", encoding="utf-8")
    os.utime(untracked_plan, (old_ts + 100.0, old_ts + 100.0))

    res3 = worktree.create_worktree(repo, "demo", init_codegraph=False)
    assert "docs/plans/demo.md" not in res3["copied_files"]
    assert wt_plan.read_text(encoding="utf-8") == "edited in worktree\n"

    # Tracked worktree file must NEVER be overwritten even if source in main has newer mtime
    wt_extra = wt / "docs" / "extra.txt"
    _git(wt, "add", "docs/extra.txt")
    _git(wt, "commit", "-m", "track extra in worktree")
    wt_extra.write_text("tracked edit in worktree\n", encoding="utf-8")
    os.utime(wt_extra, (old_ts, old_ts))
    extra_doc.write_text("main edit\n", encoding="utf-8")
    os.utime(extra_doc, (old_ts + 5000.0, old_ts + 5000.0))

    res4 = worktree.create_worktree(
        repo,
        "demo",
        copy_paths=["docs/extra.txt"],
        init_codegraph=False,
    )
    assert "docs/extra.txt" not in res4["copied_files"]
    assert wt_extra.read_text(encoding="utf-8") == "tracked edit in worktree\n"

    # Older untracked worktree copy gets updated when source is strictly newer
    untracked_spec.write_text("spec v2\n", encoding="utf-8")
    spec_dst = wt / "docs" / "specs" / "demo.md"
    os.utime(spec_dst, (old_ts, old_ts))
    os.utime(untracked_spec, (old_ts + 300.0, old_ts + 300.0))
    res5 = worktree.create_worktree(repo, "demo", init_codegraph=False)
    assert res5["copied_files"] == ["docs/specs/demo.md"]
    assert spec_dst.read_text(encoding="utf-8") == "spec v2\n"


def test_codegraph_status_outcomes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")

    # 1. skipped_no_source_index
    r1 = worktree.create_worktree(repo, "cg-1")
    assert r1["codegraph_status"] == "skipped_no_source_index"

    # Create source .codegraph directory
    (repo / ".codegraph").mkdir()

    # 2. skipped_disabled
    r2 = worktree.create_worktree(repo, "cg-2", init_codegraph=False)
    assert r2["codegraph_status"] == "skipped_disabled"

    # 3. skipped_missing_cli
    monkeypatch.setattr(worktree.shutil, "which", lambda name: None)
    r3 = worktree.create_worktree(repo, "cg-3", init_codegraph=True)
    assert r3["codegraph_status"] == "skipped_missing_cli"

    # 4. already_initialized
    r4_init = worktree.create_worktree(repo, "cg-4", init_codegraph=False)
    (Path(r4_init["worktree_path"]) / ".codegraph").mkdir()
    r4 = worktree.create_worktree(repo, "cg-4", init_codegraph=True)
    assert r4["codegraph_status"] == "already_initialized"

    # Mock shutil.which and subprocess.run ONLY for codegraph calls
    monkeypatch.setattr(worktree.shutil, "which", lambda name: "/fake/bin/codegraph" if name == "codegraph" else None)
    orig_run = subprocess.run
    seen_calls: list[tuple[list[str], dict[str, object]]] = []

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if cmd and cmd[0] == "/fake/bin/codegraph":
            seen_calls.append((cmd, kwargs))
            mode = os.environ.get("FAKE_CG_MODE", "ok")
            if mode == "ok":
                return subprocess.CompletedProcess(cmd, 0, stdout="initialized\n", stderr="")
            if mode == "fail":
                return subprocess.CompletedProcess(cmd, 2, stdout="", stderr="index build failed\ndetails")
            if mode == "timeout":
                raise subprocess.TimeoutExpired(cmd, 30)
            if mode == "oserror":
                raise OSError("permission denied")
        return orig_run(cmd, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(worktree.subprocess, "run", _fake_run)

    # 5. initialized
    monkeypatch.setenv("FAKE_CG_MODE", "ok")
    r5 = worktree.create_worktree(repo, "cg-5", init_codegraph=True)
    assert r5["codegraph_status"] == "initialized"
    assert seen_calls[-1][0] == ["/fake/bin/codegraph", "init", "-y"]
    assert seen_calls[-1][1]["cwd"] == r5["worktree_path"]
    assert seen_calls[-1][1]["timeout"] == 30
    assert seen_calls[-1][1]["shell"] is False

    # 6. failed: nonzero exit, timeout, OSError (all non-fatal)
    monkeypatch.setenv("FAKE_CG_MODE", "fail")
    r6 = worktree.create_worktree(repo, "cg-6", init_codegraph=True)
    assert r6["codegraph_status"] == "failed: index build failed"

    monkeypatch.setenv("FAKE_CG_MODE", "timeout")
    r7 = worktree.create_worktree(repo, "cg-7", init_codegraph=True)
    assert r7["codegraph_status"] == "failed: timed out after 30s"

    monkeypatch.setenv("FAKE_CG_MODE", "oserror")
    r8 = worktree.create_worktree(repo, "cg-8", init_codegraph=True)
    assert r8["codegraph_status"].startswith("failed: permission denied")


def test_list_worktrees_and_non_force_remove(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    worktree.create_worktree(repo, "clean-merged", init_codegraph=False)
    worktree.create_worktree(repo, "unmerged-wt", init_codegraph=False)
    worktree.create_worktree(repo, "dirty-wt", init_codegraph=False)
    worktree.create_worktree(repo, "locked-wt", init_codegraph=False)
    _git(repo, "worktree", "lock", str(repo / ".worktrees" / "locked-wt"))

    ext_wt = tmp_path / "ext-wt"
    _git(repo, "worktree", "add", "--detach", str(ext_wt), "main")

    listed = worktree.list_worktrees(repo)
    assert listed["repo_root"] == str(repo.resolve())
    by_slug = {w["slug"]: w for w in listed["worktrees"] if w["slug"] is not None}
    assert set(by_slug.keys()) == {"clean-merged", "unmerged-wt", "dirty-wt", "locked-wt"}
    assert by_slug["locked-wt"]["locked"] is True

    main_entries = [w for w in listed["worktrees"] if w["is_main"]]
    assert len(main_entries) == 1
    assert main_entries[0]["is_paddock_managed"] is False
    assert main_entries[0]["slug"] is None

    ext_entry = next(w for w in listed["worktrees"] if Path(w["path"]) == ext_wt.resolve())
    assert ext_entry["detached"] is True
    assert ext_entry["is_main"] is False
    assert ext_entry["is_paddock_managed"] is False
    assert ext_entry["slug"] is None

    # Refusal on external or nonexistent Paddock-managed worktree
    with pytest.raises(ValueError, match="not a registered git worktree"):
        worktree.remove_worktree(repo, "ext-wt")
    with pytest.raises(ValueError, match="not a registered git worktree"):
        worktree.remove_worktree(repo, "does-not-exist")

    # Clean remove with delete_branch=False preserves branch
    rm_keep = worktree.remove_worktree(repo, "clean-merged", delete_branch=False)
    assert rm_keep["removed"] is True
    assert rm_keep["branch"] == "feat/clean-merged"
    assert rm_keep["branch_deleted"] is False
    assert rm_keep["branch_delete_warning"] is None
    assert worktree._ref_exists(repo, "refs/heads/feat/clean-merged")

    # Reattach and remove with delete_branch=True on merged branch deletes branch via -d
    worktree.create_worktree(repo, "clean-merged", init_codegraph=False)
    rm_del = worktree.remove_worktree(repo, "clean-merged", delete_branch=True)
    assert rm_del["removed"] is True
    assert rm_del["branch_deleted"] is True
    assert rm_del["branch_delete_warning"] is None
    assert not worktree._ref_exists(repo, "refs/heads/feat/clean-merged")

    # Unmerged branch with delete_branch=True removes worktree, never runs -D, emits warning
    unmerged_path = repo / ".worktrees" / "unmerged-wt"
    (unmerged_path / "feature.txt").write_text("new commit\n", encoding="utf-8")
    _git(unmerged_path, "add", "feature.txt")
    _git(unmerged_path, "commit", "-m", "unmerged feature commit")

    rm_unmerged = worktree.remove_worktree(repo, "unmerged-wt", delete_branch=True)
    assert rm_unmerged["removed"] is True
    assert rm_unmerged["branch"] == "feat/unmerged-wt"
    assert rm_unmerged["branch_deleted"] is False
    assert rm_unmerged["branch_delete_warning"]
    assert worktree._ref_exists(repo, "refs/heads/feat/unmerged-wt")

    # Dirty worktree refusal (both untracked file and modified tracked file)
    dirty_path = repo / ".worktrees" / "dirty-wt"
    (dirty_path / "untracked.txt").write_text("keep me\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="Failed to remove worktree"):
        worktree.remove_worktree(repo, "dirty-wt", delete_branch=True)
    assert dirty_path.exists()
    (dirty_path / "untracked.txt").unlink()
    (dirty_path / "README.md").write_text("modified\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="Failed to remove worktree"):
        worktree.remove_worktree(repo, "dirty-wt")
    assert dirty_path.exists()

    # Locked worktree refusal (non-force)
    with pytest.raises(RuntimeError, match="Failed to remove worktree"):
        worktree.remove_worktree(repo, "locked-wt")
    assert (repo / ".worktrees" / "locked-wt").exists()

    # Protected branch main/master cannot be deleted even if checked out in a Paddock worktree
    _git(repo, "checkout", "-b", "temp-main-holder")
    _git(repo, "worktree", "add", str(repo / ".worktrees" / "main-wt"), "main")
    rm_main_wt = worktree.remove_worktree(repo, "main-wt", delete_branch=True)
    assert rm_main_wt["removed"] is True
    assert rm_main_wt["branch"] == "main"
    assert rm_main_wt["branch_deleted"] is False
    assert "protected branch" in (rm_main_wt["branch_delete_warning"] or "")
    assert worktree._ref_exists(repo, "refs/heads/main")


def test_standalone_worktree_module_importable(tmp_path: Path) -> None:
    src = Path(worktree.__file__).resolve()
    spec = importlib.util.spec_from_file_location("standalone_worktree", src)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["standalone_worktree"] = mod
    try:
        spec.loader.exec_module(mod)
        repo = _init_repo(tmp_path / "standalone-repo")
        assert mod.resolve_repo_root(repo) == repo.resolve()
    finally:
        sys.modules.pop("standalone_worktree", None)
