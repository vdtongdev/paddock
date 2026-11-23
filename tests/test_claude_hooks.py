"""Tests for Claude Code lifecycle hooks in platforms/claude/scripts/claude_hooks.py."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from core.memory import memory

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK_SCRIPT = REPO_ROOT / "platforms" / "claude" / "scripts" / "claude_hooks.py"


def _run_hook(
    event: str,
    payload: dict | str,
    paddock_home: Path,
    plugin_data: Path,
    extra_env: dict[str, str] | None = None,
) -> dict:
    env = os.environ.copy()
    env["PADDOCK_HOME"] = str(paddock_home)
    env["CLAUDE_PLUGIN_DATA"] = str(plugin_data)
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


def _init_git_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(repo), "init", "-b", "main"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@example.com"], check=True)
    (repo / "README.md").write_text("hi\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "init"], check=True, capture_output=True)


def test_guard_search_guard_branch_guard_and_trap_inject(tmp_path: Path) -> None:
    paddock_home = tmp_path / "paddock"
    plugin_data = tmp_path / "plugin-data"
    repo = tmp_path / "repo"
    _init_git_repo(repo)

    # Write a trap matching src/app.py
    old_home = os.environ.get("PADDOCK_HOME")
    os.environ["PADDOCK_HOME"] = str(paddock_home)
    try:
        memory.write_entry(
            repo,
            "project",
            "src-trap",
            "Check nil in src/app.py",
            {
                "type": "project",
                "trap": True,
                "paths": ["src/**"],
                "status": "active",
                "verified": "human",
                "verified_on": "2026-11-23",
                "stale_after": "2027-05-23",
            },
            "**Symptom:** Crash.\n**Cause:** Nil.\n**Do instead:** Guard.\n",
        )
    finally:
        if old_home is None:
            os.environ.pop("PADDOCK_HOME", None)
        else:
            os.environ["PADDOCK_HOME"] = old_home

    # Main session Edit blocked by guard, docs/specs allowed
    deny_edit = _run_hook(
        "pre-tool-use",
        {"cwd": str(repo), "tool_name": "Edit", "tool_input": {"file_path": str(repo / "src" / "app.py")}},
        paddock_home,
        plugin_data,
    )
    assert deny_edit["hookSpecificOutput"]["permissionDecision"] == "deny"

    allow_spec = _run_hook(
        "pre-tool-use",
        {"cwd": str(repo), "tool_name": "Write", "tool_input": {"file_path": str(repo / "docs" / "specs" / "s.md")}},
        paddock_home,
        plugin_data,
    )
    assert allow_spec == {}

    # Main session Grep/Glob/WebFetch/WebSearch blocked by search-guard; subagent allowed
    for tool in ("Grep", "Glob", "WebFetch", "WebSearch"):
        deny_search = _run_hook(
            "pre-tool-use",
            {"cwd": str(repo), "tool_name": tool, "tool_input": {}},
            paddock_home,
            plugin_data,
        )
        assert deny_search["hookSpecificOutput"]["permissionDecision"] == "deny"
        allow_sub = _run_hook(
            "pre-tool-use",
            {"cwd": str(repo), "agent_id": "agent-1", "tool_name": tool, "tool_input": {}},
            paddock_home,
            plugin_data,
        )
        assert allow_sub == {}

    # Subagent Edit blocked on main by branch-guard
    deny_branch = _run_hook(
        "pre-tool-use",
        {
            "cwd": str(repo),
            "agent_id": "agent-1",
            "tool_name": "Edit",
            "tool_input": {"file_path": str(repo / "src" / "app.py")},
        },
        paddock_home,
        plugin_data,
    )
    assert deny_branch["hookSpecificOutput"]["permissionDecision"] == "deny"

    # Subagent Edit allowed on feature branch + trap-inject returns additionalContext
    subprocess.run(["git", "-C", str(repo), "checkout", "-b", "feat/ok"], check=True, capture_output=True)
    allow_trap = _run_hook(
        "pre-tool-use",
        {
            "cwd": str(repo),
            "agent_id": "agent-1",
            "tool_name": "Edit",
            "tool_input": {"file_path": str(repo / "src" / "app.py")},
        },
        paddock_home,
        plugin_data,
    )
    assert allow_trap["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert "src-trap" in allow_trap["hookSpecificOutput"]["additionalContext"]


def test_plan_gate_prompt_record_and_plan_record(tmp_path: Path) -> None:
    paddock_home = tmp_path / "paddock"
    plugin_data = tmp_path / "plugin-data"
    repo = tmp_path / "repo"
    (repo / "docs" / "specs").mkdir(parents=True)
    (repo / "docs" / "plans").mkdir(parents=True)

    spec_file = repo / "docs" / "specs" / "f.md"
    plan_file = repo / "docs" / "plans" / "f.md"
    spec_file.write_text("# Spec\n", encoding="utf-8")
    plan_file.write_text("### Task 1\n- Agent: implementer\n- Files: a.py\n", encoding="utf-8")

    # Record user prompt BEFORE editing file -> unapproved!
    _run_hook("user-prompt-submit", {"session_id": "s1", "cwd": str(repo)}, paddock_home, plugin_data)
    future = time.time() + 120.0
    os.utime(spec_file, (future, future))
    os.utime(plan_file, (future, future))

    deny_planner = _run_hook(
        "pre-tool-use",
        {
            "session_id": "s1",
            "cwd": str(repo),
            "tool_name": "Agent",
            "tool_input": {"subagent_type": "paddock:planner", "prompt": "Plan docs/specs/f.md"},
        },
        paddock_home,
        plugin_data,
    )
    assert deny_planner["hookSpecificOutput"]["permissionDecision"] == "deny"

    deny_builder = _run_hook(
        "pre-tool-use",
        {
            "session_id": "s1",
            "cwd": str(repo),
            "tool_name": "Agent",
            "tool_input": {"subagent_type": "paddock:builder", "prompt": "Build docs/plans/f.md"},
        },
        paddock_home,
        plugin_data,
    )
    assert deny_builder["hookSpecificOutput"]["permissionDecision"] == "deny"

    # Bring file mtime earlier than user prompt -> approved
    past = time.time() - 120.0
    os.utime(spec_file, (past, past))
    os.utime(plan_file, (past, past))

    assert (
        _run_hook(
            "pre-tool-use",
            {
                "session_id": "s1",
                "cwd": str(repo),
                "tool_name": "Agent",
                "tool_input": {"subagent_type": "paddock:planner", "prompt": "Plan docs/specs/f.md"},
            },
            paddock_home,
            plugin_data,
        )
        == {}
    )
    assert (
        _run_hook(
            "pre-tool-use",
            {
                "session_id": "s1",
                "cwd": str(repo),
                "tool_name": "Agent",
                "tool_input": {"subagent_type": "paddock:builder", "prompt": "Build docs/plans/f.md"},
            },
            paddock_home,
            plugin_data,
        )
        == {}
    )

    # plan-record records docs/plans/f.md into plans.json
    _run_hook(
        "post-tool-use",
        {"session_id": "s1", "cwd": str(repo), "tool_name": "Write", "tool_input": {"file_path": "docs/plans/f.md"}},
        paddock_home,
        plugin_data,
    )
    plans_data = json.loads((plugin_data / "plans.json").read_text(encoding="utf-8"))
    assert str(plan_file.resolve()) in plans_data["s1"]

    # Mode: git rejects --tags
    deny_git = _run_hook(
        "pre-tool-use",
        {
            "session_id": "s1",
            "cwd": str(repo),
            "tool_name": "Agent",
            "tool_input": {"subagent_type": "implementer", "prompt": "Mode: git push --tags"},
        },
        paddock_home,
        plugin_data,
    )
    assert deny_git["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_session_start_subagent_start_evidence_check_solo_and_fail_open(tmp_path: Path) -> None:
    paddock_home = tmp_path / "paddock"
    plugin_data = tmp_path / "plugin-data"
    repo = tmp_path / "repo"
    repo.mkdir()

    old_home = os.environ.get("PADDOCK_HOME")
    os.environ["PADDOCK_HOME"] = str(paddock_home)
    try:
        memory.write_entry(
            repo,
            "user",
            "u-pref",
            "User fact",
            {"type": "user", "status": "active", "verified": "human", "verified_on": "2026-11-23"},
            "Short.\n",
        )
        memory.write_entry(
            repo,
            "project",
            "p-trap",
            "Proj trap",
            {
                "type": "project",
                "trap": True,
                "status": "active",
                "verified": "human",
                "verified_on": "2026-11-23",
                "stale_after": "2020-01-01",
            },
            "**Symptom:** S.\n**Cause:** C.\n**Do instead:** D.\n",
        )
    finally:
        if old_home is None:
            os.environ.pop("PADDOCK_HOME", None)
        else:
            os.environ["PADDOCK_HOME"] = old_home

    ss = _run_hook("session-start", {"cwd": str(repo)}, paddock_home, plugin_data)
    ss_ctx = ss["hookSpecificOutput"]["additionalContext"]
    assert "PADDOCK ORCHESTRATOR RULES" in ss_ctx
    assert "u-pref" in ss_ctx and "p-trap" in ss_ctx and "Stale Memory Reminders" in ss_ctx

    sub = _run_hook("subagent-start", {"cwd": str(repo)}, paddock_home, plugin_data)
    sub_ctx = sub["hookSpecificOutput"]["additionalContext"]
    assert "PADDOCK VERIFY RULES" in sub_ctx and "p-trap" in sub_ctx

    # Evidence-check: missing sections blocked; verifier duplicate verdicts blocked; valid passes
    tfile = tmp_path / "agent.jsonl"
    tfile.write_text(json.dumps({"role": "assistant", "content": "Done without evidence"}) + "\n", encoding="utf-8")
    assert (
        _run_hook(
            "subagent-stop",
            {"agent_transcript_path": str(tfile), "agent_type": "implementer"},
            paddock_home,
            plugin_data,
        )["decision"]
        == "block"
    )

    tfile.write_text(
        json.dumps({"role": "assistant", "content": "## Evidence\n- ok\n## Unverified\nNone.\nPASS\nFAIL: impl\n"}) + "\n",
        encoding="utf-8",
    )
    assert (
        _run_hook(
            "subagent-stop",
            {"agent_transcript_path": str(tfile), "agent_type": "verifier"},
            paddock_home,
            plugin_data,
        )["decision"]
        == "block"
    )

    tfile.write_text(
        json.dumps({"role": "assistant", "content": "## Evidence\n- ok\n## Unverified\nNone.\nPASS\n"}) + "\n",
        encoding="utf-8",
    )
    assert (
        _run_hook(
            "subagent-stop",
            {"agent_transcript_path": str(tfile), "agent_type": "verifier"},
            paddock_home,
            plugin_data,
        )
        == {}
    )

    # stop_hook_active=True allows even when missing
    assert (
        _run_hook(
            "subagent-stop",
            {"stop_hook_active": True, "last_assistant_message": "incomplete"},
            paddock_home,
            plugin_data,
        )
        == {}
    )

    # PADDOCK_SOLO=1 bypasses guard + UTF-8 non-ASCII input handled cleanly
    assert (
        _run_hook(
            "pre-tool-use",
            {"cwd": str(repo), "tool_name": "Edit", "tool_input": {"file_path": str(repo / "src" / "a–b.py")}},
            paddock_home,
            plugin_data,
            extra_env={"PADDOCK_SOLO": "1"},
        )
        == {}
    )

    # Garbage stdin fails open with exit 0
    assert _run_hook("pre-tool-use", "{bad json", paddock_home, plugin_data) == {}
