"""Unit and regression tests for core/memory/memory.py."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from core.memory import memory


def _valid_meta(entry_type: str = "project", **overrides: object) -> dict[str, object]:
    meta: dict[str, object] = {
        "type": entry_type,
        "paths": ["core/**"],
        "topics": ["memory"],
        "status": "active",
        "verified": "human",
        "verified_on": "2026-11-23",
        "stale_after": "2027-05-23",
    }
    meta.update(overrides)
    return meta


def test_paddock_home_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PADDOCK_HOME", str(tmp_path / "custom-paddock"))
    assert memory.paddock_home() == (tmp_path / "custom-paddock").resolve()
    assert memory.layer_dir(tmp_path, "user") == (tmp_path / "custom-paddock" / "memory").resolve()


def test_parse_and_render_roundtrip_block_lists_and_traps() -> None:
    raw = """---
name: wsl-stops-postgres
description: WSL stops Postgres between sessions
metadata:
  type: reference
  trap: true
  paths:
    - scripts/dev_db.ps1
    - tests/**
  topics: [wsl, postgres]
  status: active
  verified: human
  verified_on: 2026-11-23
  stale_after: never
---
**Symptom:** Connection refused.
**Cause:** WSL VM suspended.
**Do instead:** Run `wsl -d Ubuntu service postgresql start`.
See [[other-entry]].
"""
    parsed, body = memory.parse_entry_text(raw)
    assert parsed["name"] == "wsl-stops-postgres"
    assert parsed["metadata"]["trap"] is True
    assert parsed["metadata"]["paths"] == ["scripts/dev_db.ps1", "tests/**"]
    assert parsed["metadata"]["topics"] == ["wsl", "postgres"]
    errs = memory.validate_entry_data(
        parsed["name"],
        parsed["description"],
        parsed["metadata"],
        body,
        layer="project",
        file_stem="wsl-stops-postgres",
    )
    assert errs == []

    rerendered = memory.render_entry(
        parsed["name"], parsed["description"], parsed["metadata"], body
    )
    parsed2, body2 = memory.parse_entry_text(rerendered)
    assert parsed2 == parsed
    assert body2 == body


def test_validation_rejects_bad_entries_and_path_traversal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PADDOCK_HOME", str(tmp_path / "paddock"))

    for bad_name in ("../escape", "sub/name", r"sub\name", "BadCamel", "has space", ""):
        with pytest.raises(ValueError):
            memory.write_entry(
                tmp_path,
                "user",
                bad_name,
                "desc",
                _valid_meta("feedback"),
                "Body.\n",
            )

    # Missing stale_after on project/reference must fail BEFORE creating file
    with pytest.raises(ValueError, match="stale_after"):
        meta_no_stale = _valid_meta("project")
        del meta_no_stale["stale_after"]
        memory.write_entry(tmp_path, "project", "missing-stale", "desc", meta_no_stale, "Body.\n")
    assert not memory.layer_dir(tmp_path, "project").exists()

    # Layer mismatch: project type in user layer
    with pytest.raises(ValueError, match="not allowed in 'user' layer"):
        memory.write_entry(tmp_path, "user", "wrong-layer", "desc", _valid_meta("project"), "Body.\n")

    # Trap missing required sections
    with pytest.raises(ValueError, match="Symptom"):
        memory.write_entry(
            tmp_path,
            "project",
            "bad-trap",
            "desc",
            _valid_meta("project", trap=True),
            "Missing structured headings.\n",
        )


def test_git_worktree_shares_repo_slug(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PADDOCK_HOME", str(tmp_path / "paddock"))
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-b", "main"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@example.com"], check=True)
    (repo / "README.md").write_text("hi\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "init"], check=True, capture_output=True)

    wt = tmp_path / "wt-feat"
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "-b", "feat/wt", str(wt), "main"],
        check=True,
        capture_output=True,
    )

    assert memory.resolve_repo_root(repo) == memory.resolve_repo_root(wt)
    assert memory.repo_slug(repo) == memory.repo_slug(wt)
    assert memory.layer_dir(repo, "project") == memory.layer_dir(wt, "project")


def test_select_ordering_globs_and_dangling_links(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PADDOCK_HOME", str(tmp_path / "paddock"))
    repo = tmp_path / "repo"
    repo.mkdir()

    # User layer trap + feedback
    memory.write_entry(
        repo,
        "user",
        "user-pref",
        "Prefers terse output",
        _valid_meta("feedback", paths=[], topics=["style"]),
        "Be concise.\n",
    )
    # Project trap + non-trap
    res = memory.write_entry(
        repo,
        "project",
        "proj-trap",
        "Avoid crlf on stdio",
        _valid_meta("project", trap=True, paths=["core/**/*.py"], topics=["mcp"]),
        "**Symptom:** CRLF breaks parser.\n**Cause:** Text mode.\n**Do instead:** Use buffer.\nSee [[missing-target]].\n",
    )
    assert res["dangling_links"] == ["missing-target"]

    memory.write_entry(
        repo,
        "project",
        "proj-note",
        "Architecture overview",
        _valid_meta("project", trap=False, paths=["core/memory/memory.py"], topics=["arch"]),
        "Details.\n",
    )

    # Query matching core/memory/memory.py and style topic: project layer first, traps first
    sel = memory.select(repo, paths=[str(repo / "core" / "memory" / "memory.py")], topics=["style"])
    names = [e["name"] for e in sel["entries"]]
    assert names == ["proj-trap", "proj-note", "user-pref"]
    assert sel["entries"][0]["match_reasons"] == ["path:core/**/*.py"]

    # Non-matching trap-only check
    traps = memory.select(repo, traps_only=True)
    assert [e["name"] for e in traps["entries"]] == ["proj-trap"]


def test_foreman_bug1_memory_md_backup_is_notice_not_problem(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PADDOCK_HOME", str(tmp_path / "paddock"))
    mdir = memory.layer_dir(tmp_path, "user")
    mdir.mkdir(parents=True, exist_ok=True)
    (mdir / "MEMORY.md").write_text("# Hand-written notes\n- keep me\n", encoding="utf-8")

    res = memory.index(tmp_path)
    assert res["problems"] == []
    assert any("MEMORY.md.bak" in n for n in res["notices"])
    assert (mdir / "MEMORY.md.bak").read_text(encoding="utf-8") == "# Hand-written notes\n- keep me\n"
    assert memory.GENERATED_MARKER in (mdir / "MEMORY.md").read_text(encoding="utf-8")

    # Second index/validate ignores MEMORY.md.bak cleanly
    assert memory.validate(tmp_path)["problems"] == []


def test_foreman_bug2_deprecate_exact_path_matching_and_link_rewrite(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PADDOCK_HOME", str(tmp_path / "paddock"))
    memory.write_entry(
        tmp_path,
        "project",
        "foo",
        "Old approach",
        _valid_meta("project"),
        "Legacy.\n",
    )
    memory.write_entry(
        tmp_path,
        "project",
        "bar",
        "New approach",
        _valid_meta("project"),
        "Replacement.\n",
    )
    memory.write_entry(
        tmp_path,
        "project",
        "caller-note",
        "References old",
        _valid_meta("project"),
        "Use [[foo]] carefully.\n",
    )
    # Create an unrelated broken entry whose filename contains "foo" as a substring!
    broken = memory.layer_dir(tmp_path, "project") / "foobar.md"
    broken.write_text("---\nname: wrong-name\n---\nbroken\n", encoding="utf-8")

    dep = memory.deprecate(tmp_path, "foo", superseded_by="bar")
    assert dep["deprecated"] == "foo"
    assert dep["links_rewritten"] == 1
    assert len(dep["problems"]) == 1  # foobar.md problem reported, did NOT abort deprecate("foo")!

    caller_parsed, caller_body = memory.parse_entry_text(
        (memory.layer_dir(tmp_path, "project") / "caller-note.md").read_text(encoding="utf-8")
    )
    assert "[[bar]]" in caller_body
    assert "[[foo]]" not in caller_body
    assert caller_parsed["metadata"]["status"] == "active"

    foo_parsed, _ = memory.parse_entry_text(
        (memory.layer_dir(tmp_path, "project") / "foo.md").read_text(encoding="utf-8")
    )
    assert foo_parsed["metadata"]["status"] == "deprecated"
    assert foo_parsed["metadata"]["superseded_by"] == "bar"


def test_foreman_bug3_truncation_cap_no_leading_semicolon(tmp_path: Path) -> None:
    huge = [{"file": str(tmp_path / "a.md"), "name": "a", "error": "X" * 5000}]
    out = memory.format_problems_capped(huge, limit=3000)
    assert len(out) <= 3000
    assert not out.startswith(";")
    assert out.endswith("...")

    many = [
        {"file": str(tmp_path / f"e-{i}.md"), "name": f"e-{i}", "error": "E" * 400}
        for i in range(20)
    ]
    capped = memory.format_problems_capped(many, limit=1200)
    assert len(capped) <= 1200
    assert "; ... " in capped
    assert not capped.startswith(";")


def test_stale_filtering(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PADDOCK_HOME", str(tmp_path / "paddock"))
    memory.write_entry(
        tmp_path,
        "project",
        "old-fact",
        "Past expiry",
        _valid_meta("project", stale_after="2026-10-01"),
        "Stale.\n",
    )
    memory.write_entry(
        tmp_path,
        "project",
        "fresh-fact",
        "Still valid",
        _valid_meta("project", stale_after="2027-01-01"),
        "Fresh.\n",
    )
    memory.write_entry(
        tmp_path,
        "project",
        "forever-fact",
        "Never expires",
        _valid_meta("project", stale_after="never"),
        "Eternal.\n",
    )
    res = memory.stale(tmp_path, today="2026-11-23")
    assert [e["name"] for e in res["entries"]] == ["old-fact"]


def test_import_claude_read_only_and_overwrite(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PADDOCK_HOME", str(tmp_path / "paddock"))
    claude_home = tmp_path / "claude"
    repo = tmp_path / "repo"
    repo.mkdir()
    slug = memory.repo_slug(repo)

    (claude_home / "memory").mkdir(parents=True)
    (claude_home / "projects" / slug / "memory").mkdir(parents=True)

    user_src = claude_home / "memory" / "user-tone.md"
    user_src_text = memory.render_entry("user-tone", "Direct", _valid_meta("user"), "Short.\n")
    user_src.write_text(user_src_text, encoding="utf-8")

    proj_src = claude_home / "projects" / slug / "memory" / "repo-rule.md"
    proj_src_text = memory.render_entry("repo-rule", "Use uv", _valid_meta("project"), "Uv.\n")
    proj_src.write_text(proj_src_text, encoding="utf-8")

    dry = memory.import_claude(cwd=repo, dry_run=True, claude_home=claude_home)
    assert len(dry["copied"]) == 2
    assert not (memory.paddock_home() / "memory" / "user-tone.md").exists()

    first = memory.import_claude(cwd=repo, claude_home=claude_home)
    assert len(first["copied"]) == 2
    assert (memory.paddock_home() / "memory" / "user-tone.md").is_file()
    assert (memory.layer_dir(repo, "project") / "repo-rule.md").is_file()
    assert (memory.paddock_home() / "memory" / "MEMORY.md").is_file()

    # Existing destinations skipped by default
    (memory.paddock_home() / "memory" / "user-tone.md").write_text("edited", encoding="utf-8")
    second = memory.import_claude(cwd=repo, claude_home=claude_home)
    assert len(second["skipped"]) == 2
    assert (memory.paddock_home() / "memory" / "user-tone.md").read_text(encoding="utf-8") == "edited"

    # Overwrite updates destination while source files remain untouched
    third = memory.import_claude(cwd=repo, overwrite=True, claude_home=claude_home)
    assert len(third["copied"]) == 2
    assert (memory.paddock_home() / "memory" / "user-tone.md").read_text(encoding="utf-8") == user_src_text
    assert user_src.read_text(encoding="utf-8") == user_src_text
    assert proj_src.read_text(encoding="utf-8") == proj_src_text
