"""Tests for platforms/antigravity/scripts/agy_hooks.py."""

from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from core.memory import memory
from platforms.antigravity.scripts import agy_hooks

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK_SCRIPT = REPO_ROOT / "platforms" / "antigravity" / "scripts" / "agy_hooks.py"


def _run_hook(
    event: str,
    payload: dict | str,
    paddock_home: Path,
    extra_env: dict[str, str] | None = None,
) -> dict:
    env = os.environ.copy()
    env["PADDOCK_HOME"] = str(paddock_home)
    env.pop("PADDOCK_SOLO", None)
    if extra_env:
        env.update(extra_env)
    raw_in = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    proc = subprocess.run(
        [sys.executable, str(HOOK_SCRIPT), event],
        input=raw_in.encode("utf-8"),
        capture_output=True,
        env=env,
    )
    assert proc.returncode == 0
    return json.loads(proc.stdout.decode("utf-8"))


def _write_transcript(path: Path, records: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
        encoding="utf-8",
    )


def _init_git_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(repo), "init", "-b", "main"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@example.com"], check=True)
    (repo / "README.md").write_text("hi\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "init"], check=True, capture_output=True)


def test_session_start_injects_both_layers_and_caps_problems(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    paddock_home = tmp_path / "paddock"
    monkeypatch.setenv("PADDOCK_HOME", str(paddock_home))
    repo = tmp_path / "repo"
    repo.mkdir()

    memory.write_entry(
        repo,
        "user",
        "user-pref",
        "Keep answers short",
        {
            "type": "user",
            "status": "active",
            "verified": "human",
            "verified_on": "2026-11-23",
        },
        "Concise.\n",
    )
    memory.write_entry(
        repo,
        "project",
        "proj-fact",
        "Old fact",
        {
            "type": "project",
            "status": "active",
            "verified": "model",
            "verified_on": "2026-11-23",
            "stale_after": "2020-01-01",
        },
        "Stale.\n",
    )

    resp = _run_hook(
        "SessionStart",
        {"hookEventName": "SessionStart", "cwd": str(repo)},
        paddock_home,
    )
    msg = resp["injectSteps"][0]["systemMessage"]["systemMessage"]
    assert "user-pref" in msg
    assert "proj-fact" in msg
    assert "Stale Memory Reminders" in msg
    assert len(msg) <= agy_hooks.MAX_SESSION_MSG_CHARS


def test_branch_guard_blocks_main_and_allows_exempt_and_feature_branch(tmp_path: Path) -> None:
    paddock_home = tmp_path / "paddock"
    repo = tmp_path / "repo"
    _init_git_repo(repo)

    # Blocked on main for src/app.py
    deny = _run_hook(
        "PreToolUse",
        {
            "hookEventName": "PreToolUse",
            "cwd": str(repo),
            "toolCall": {"name": "edit_file", "args": {"Path": str(repo / "src" / "app.py")}},
        },
        paddock_home,
    )
    assert deny["decision"] == "deny"

    # Allowed on main for docs/specs/ and docs/plans/ and ~/.paddock
    for exempt in (
        repo / "docs" / "specs" / "s.md",
        repo / "docs" / "plans" / "p.md",
        paddock_home / "memory" / "u.md",
    ):
        allow = _run_hook(
            "PreToolUse",
            {
                "hookEventName": "PreToolUse",
                "cwd": str(repo),
                "toolCall": {"name": "write_file", "args": {"Path": str(exempt)}},
            },
            paddock_home,
        )
        assert allow["decision"] == "allow"

    # Allowed on feature branch
    subprocess.run(["git", "-C", str(repo), "checkout", "-b", "feat/x"], check=True, capture_output=True)
    allow_feat = _run_hook(
        "PreToolUse",
        {
            "hookEventName": "PreToolUse",
            "cwd": str(repo),
            "toolCall": {"name": "edit_file", "args": {"Path": str(repo / "src" / "app.py")}},
        },
        paddock_home,
    )
    assert allow_feat["decision"] == "allow"


def test_plan_gate_approval_mtime_modes_utf8_and_overwrite(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    paddock_home = tmp_path / "paddock"
    monkeypatch.setenv("PADDOCK_HOME", str(paddock_home))
    repo = tmp_path / "repo"
    (repo / "docs" / "specs").mkdir(parents=True)
    (repo / "docs" / "plans").mkdir(parents=True)

    # Write a matching trap for src/app.py
    memory.write_entry(
        repo,
        "project",
        "app-trap",
        "Watch out for app.py",
        {
            "type": "project",
            "trap": True,
            "paths": ["src/**"],
            "status": "active",
            "verified": "human",
            "verified_on": "2026-11-23",
            "stale_after": "2027-05-23",
        },
        "**Symptom:** Crash.\n**Cause:** Nil.\n**Do instead:** Guard nil.\n",
    )

    spec_file = repo / "docs" / "specs" / "feat.md"
    plan_file = repo / "docs" / "plans" / "feat.md"
    spec_file.write_text("# Spec\n", encoding="utf-8")
    plan_file.write_text("### Task 1\n- Agent: paddock-implementer\n- Files: src/app.py\n", encoding="utf-8")

    # Set spec/plan mtimes to 10:00:00 UTC, transcript USER_INPUT at 09:59:00 UTC (unapproved!)
    t_unapproved = datetime.datetime(2026, 11, 23, 9, 59, 0, tzinfo=datetime.timezone.utc)
    t_file = t_unapproved.timestamp() + 60.0
    os.utime(spec_file, (t_file, t_file))
    os.utime(plan_file, (t_file, t_file))

    transcript = tmp_path / "transcript.jsonl"
    _write_transcript(
        transcript,
        [
            {
                "step_index": 0,
                "source": "USER_EXPLICIT",
                "type": "USER_INPUT",
                "created_at": t_unapproved.isoformat().replace("+00:00", "Z"),
                "content": "Spikes 1–3 with UTF-8 en-dash",
            }
        ],
    )

    # Unapproved spec blocks paddock-planner
    deny_spec = _run_hook(
        "PreToolUse",
        {
            "hookEventName": "PreToolUse",
            "cwd": str(repo),
            "transcriptPath": str(transcript),
            "lastUserInput": "Spikes 1–3",
            "toolCall": {
                "name": "invoke_subagent",
                "args": {"TypeName": "paddock-planner", "Prompt": "Plan docs/specs/feat.md"},
            },
        },
        paddock_home,
    )
    assert deny_spec["decision"] == "deny" and "Approval Gate 1" in deny_spec["reason"]

    # Unapproved plan blocks run_workflow build_plan.py
    deny_plan = _run_hook(
        "PreToolUse",
        {
            "hookEventName": "PreToolUse",
            "cwd": str(repo),
            "transcriptPath": str(transcript),
            "toolCall": {
                "name": "run_workflow",
                "args": {"ScriptPath": "workflows/build_plan.py", "Args": {"plan_path": "docs/plans/feat.md"}},
            },
        },
        paddock_home,
    )
    assert deny_plan["decision"] == "deny" and "Approval Gate 2" in deny_plan["reason"]

    # Repeated identical "ok" approval after file mtimes unblocks both gates
    t_approved = datetime.datetime.fromtimestamp(t_file + 5.0, tz=datetime.timezone.utc)
    _write_transcript(
        transcript,
        [
            {
                "step_index": 0,
                "source": "USER_EXPLICIT",
                "type": "USER_INPUT",
                "created_at": t_unapproved.isoformat().replace("+00:00", "Z"),
                "content": "ok",
            },
            {
                "step_index": 5,
                "source": "USER_EXPLICIT",
                "type": "USER_INPUT",
                "created_at": t_approved.isoformat().replace("+00:00", "Z"),
                "content": "ok",
            },
        ],
    )

    allow_wf = _run_hook(
        "PreToolUse",
        {
            "hookEventName": "PreToolUse",
            "cwd": str(repo),
            "transcriptPath": str(transcript),
            "toolCall": {
                "name": "run_workflow",
                "args": {"ScriptPath": "workflows/build_plan.py", "Args": {"plan_path": "docs/plans/feat.md"}},
            },
        },
        paddock_home,
    )
    assert allow_wf == {"decision": "allow"}

    # Missing mode / plan denied for implementer
    deny_mode = _run_hook(
        "PreToolUse",
        {
            "hookEventName": "PreToolUse",
            "cwd": str(repo),
            "transcriptPath": str(transcript),
            "toolCall": {
                "name": "invoke_subagent",
                "args": {"TypeName": "paddock-implementer", "Prompt": "Fix src/app.py"},
            },
        },
        paddock_home,
    )
    assert deny_mode["decision"] == "deny"

    # Mode: git rejects --tags and literal commit -m
    deny_git = _run_hook(
        "PreToolUse",
        {
            "hookEventName": "PreToolUse",
            "cwd": str(repo),
            "transcriptPath": str(transcript),
            "toolCall": {
                "name": "invoke_subagent",
                "args": {"TypeName": "paddock-implementer", "Prompt": "Mode: git push --tags"},
            },
        },
        paddock_home,
    )
    assert deny_git["decision"] == "deny"

    # Valid Mode: quick triggers flat overwrite injecting VERIFY_RULES + matched trap
    allow_ov = _run_hook(
        "PreToolUse",
        {
            "hookEventName": "PreToolUse",
            "cwd": str(repo),
            "transcriptPath": str(transcript),
            "toolCall": {
                "name": "invoke_subagent",
                "args": {"TypeName": "paddock-implementer", "Prompt": "Mode: quick fix src/app.py"},
            },
        },
        paddock_home,
    )
    assert allow_ov["decision"] == "allow"
    new_prompt = allow_ov["overwrite"]["Prompt"]
    assert agy_hooks.INJECTED_MARKER in new_prompt
    assert "app-trap" in new_prompt

    # Second call with marker already present does not double-inject
    no_dup = _run_hook(
        "PreToolUse",
        {
            "hookEventName": "PreToolUse",
            "cwd": str(repo),
            "transcriptPath": str(transcript),
            "toolCall": {
                "name": "invoke_subagent",
                "args": {"TypeName": "paddock-implementer", "Prompt": new_prompt},
            },
        },
        paddock_home,
    )
    assert "overwrite" not in no_dup


def test_stop_solo_and_fail_open(tmp_path: Path) -> None:
    paddock_home = tmp_path / "paddock"
    transcript = tmp_path / "sub_transcript.jsonl"

    # Paddock subagent missing ## Evidence -> continue once
    _write_transcript(
        transcript,
        [
            {"step_index": 0, "type": "SYSTEM_MESSAGE", "content": f"{agy_hooks.INJECTED_MARKER}\nDo task."},
            {"step_index": 1, "type": "PLANNER_RESPONSE", "content": "All done."},
        ],
    )
    bounce = _run_hook(
        "Stop",
        {
            "hookEventName": "Stop",
            "continuationActive": False,
            "terminationReason": "NO_TOOL_CALL",
            "transcriptPath": str(transcript),
        },
        paddock_home,
    )
    assert bounce["decision"] == "continue"

    # continuationActive=True -> allow
    assert (
        _run_hook(
            "Stop",
            {
                "hookEventName": "Stop",
                "continuationActive": True,
                "terminationReason": "NO_TOOL_CALL",
                "transcriptPath": str(transcript),
            },
            paddock_home,
        )["decision"]
        == "allow"
    )

    # Root / run_workflow (USER_INPUT at line 0) -> allow even without ## Evidence
    _write_transcript(
        transcript,
        [
            {"step_index": 0, "type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "Hello"},
            {"step_index": 1, "type": "PLANNER_RESPONSE", "content": "Finished."},
        ],
    )
    assert (
        _run_hook(
            "Stop",
            {
                "hookEventName": "Stop",
                "continuationActive": False,
                "terminationReason": "NO_TOOL_CALL",
                "transcriptPath": str(transcript),
            },
            paddock_home,
        )["decision"]
        == "allow"
    )

    # PADDOCK_SOLO=1 bypasses enforcement
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    solo = _run_hook(
        "PreToolUse",
        {
            "hookEventName": "PreToolUse",
            "cwd": str(repo),
            "toolCall": {"name": "edit_file", "args": {"Path": str(repo / "src" / "x.py")}},
        },
        paddock_home,
        extra_env={"PADDOCK_SOLO": "1"},
    )
    assert solo == {"decision": "allow"}

    # Garbage stdin fails open with exit 0
    fail_open = _run_hook("PreToolUse", "{not-json", paddock_home)
    assert fail_open == {"decision": "allow"}
