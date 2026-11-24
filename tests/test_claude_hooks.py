"""Tests for Claude Code lifecycle hooks in platforms/claude/scripts/claude_hooks.py."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from core.config import models
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


def test_session_start_claude_models_summary_and_project_mismatch_flag(tmp_path: Path) -> None:
    paddock_home = tmp_path / "paddock"
    plugin_data = tmp_path / "plugin-data"
    repo = tmp_path / "repo"
    repo.mkdir()

    # Default balanced models match static frontmatter (no mismatch flag)
    ss_default = _run_hook("session-start", {"cwd": str(repo)}, paddock_home, plugin_data)
    ctx_default = ss_default["hookSpecificOutput"]["additionalContext"]
    assert "### Paddock Claude Models" in ctx_default
    assert "generalist=haiku/medium" in ctx_default
    assert "implementer=sonnet/medium" in ctx_default
    assert "verifier=sonnet/medium" in ctx_default
    assert "Project overrides differing from installed static frontmatter:" not in ctx_default

    # Global-only budget preset updates summary without flagging project-override mismatch
    models.set_preset("budget", scope="global", cwd=repo, home=paddock_home)
    ss_global = _run_hook("session-start", {"cwd": str(repo)}, paddock_home, plugin_data)
    ctx_global = ss_global["hookSpecificOutput"]["additionalContext"]
    assert "implementer=haiku/high" in ctx_global
    assert "ui=sonnet/low" in ctx_global
    assert "verifier=haiku/high" in ctx_global
    assert "Project overrides differing from installed static frontmatter:" not in ctx_global

    # Project overrides differing from static frontmatter trigger explicit mismatch flags
    models.set_override("claude", "implementer", model="opus", effort="high", scope="project", cwd=repo, home=paddock_home)
    models.set_role("ui", "workhorse", effort="high", scope="project", cwd=repo, home=paddock_home)
    ss_proj = _run_hook("session-start", {"cwd": str(repo)}, paddock_home, plugin_data)
    ctx_proj = ss_proj["hookSpecificOutput"]["additionalContext"]
    assert "implementer=opus/high" in ctx_proj
    assert "ui=sonnet/high" in ctx_proj
    assert "Project overrides differing from installed static frontmatter:" in ctx_proj
    assert "implementer: resolved opus/high (project-override) vs static frontmatter sonnet/medium" in ctx_proj
    assert "ui: resolved sonnet/high (project-tier) vs static frontmatter sonnet/medium" in ctx_proj


def test_pre_tool_use_agent_project_override_additional_context_and_denials(tmp_path: Path) -> None:
    paddock_home = tmp_path / "paddock"
    plugin_data = tmp_path / "plugin-data"
    repo = tmp_path / "repo"
    (repo / "docs" / "specs").mkdir(parents=True)
    (repo / "docs" / "plans").mkdir(parents=True)

    spec_file = repo / "docs" / "specs" / "f.md"
    plan_ok = repo / "docs" / "plans" / "ok.md"
    plan_unapproved = repo / "docs" / "plans" / "unapproved.md"
    spec_file.write_text("# Spec\n", encoding="utf-8")
    plan_ok.write_text("### Task 1\n- Agent: implementer\n- Files: a.py\n", encoding="utf-8")
    plan_unapproved.write_text("### Task 1\n- Agent: implementer\n- Files: a.py\n", encoding="utf-8")

    _run_hook("user-prompt-submit", {"session_id": "s1", "cwd": str(repo)}, paddock_home, plugin_data)
    past = time.time() - 120.0
    future = time.time() + 120.0
    os.utime(plan_ok, (past, past))
    os.utime(spec_file, (future, future))
    os.utime(plan_unapproved, (future, future))

    # Global config alone does not emit project-override additionalContext
    models.set_override("claude", "implementer", model="opus", scope="global", cwd=repo, home=paddock_home)
    assert (
        _run_hook(
            "pre-tool-use",
            {
                "session_id": "s1",
                "cwd": str(repo),
                "tool_name": "Agent",
                "tool_input": {"subagent_type": "paddock:implementer", "prompt": "Mode: quick fix a.py"},
            },
            paddock_home,
            plugin_data,
        )
        == {}
    )

    # Project overrides differing from static frontmatter emit non-blocking allow + additionalContext
    models.set_override("claude", "implementer", model="opus", effort="high", scope="project", cwd=repo, home=paddock_home)
    models.set_role("ui", "workhorse", effort="high", scope="project", cwd=repo, home=paddock_home)
    # Project override matching static frontmatter (sonnet/medium) does not emit mismatch warning
    models.set_override("claude", "builder", model="sonnet", effort="medium", scope="project", cwd=repo, home=paddock_home)

    impl_allow = _run_hook(
        "pre-tool-use",
        {
            "session_id": "s1",
            "cwd": str(repo),
            "tool_name": "Agent",
            "tool_input": {"subagent_type": "paddock:implementer", "prompt": "Mode: quick fix a.py"},
        },
        paddock_home,
        plugin_data,
    )
    impl_out = impl_allow["hookSpecificOutput"]
    assert impl_out["permissionDecision"] == "allow"
    assert "opus/high" in impl_out["additionalContext"]
    assert "sonnet/medium" in impl_out["additionalContext"]
    assert 'model: "opus"' in impl_out["additionalContext"]
    assert "agents/implementer.md" in impl_out["additionalContext"]

    ui_allow = _run_hook(
        "pre-tool-use",
        {
            "session_id": "s1",
            "cwd": str(repo),
            "tool_name": "Agent",
            "tool_input": {"subagent_type": "ui", "prompt": "Run docs/plans/ok.md"},
        },
        paddock_home,
        plugin_data,
    )
    ui_out = ui_allow["hookSpecificOutput"]
    assert ui_out["permissionDecision"] == "allow"
    assert "effort override" in ui_out["additionalContext"]
    assert "sonnet/high" in ui_out["additionalContext"]
    assert "agents/ui.md" in ui_out["additionalContext"]

    # Roles without mismatch and non-Paddock agent types stay clean ({})
    assert (
        _run_hook(
            "pre-tool-use",
            {
                "session_id": "s1",
                "cwd": str(repo),
                "tool_name": "Agent",
                "tool_input": {"subagent_type": "generalist", "prompt": "Mode: lookup find docs"},
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
                "tool_input": {"subagent_type": "paddock:builder", "prompt": "Build docs/plans/ok.md"},
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
                "tool_input": {"subagent_type": "Explore", "prompt": "Find files"},
            },
            paddock_home,
            plugin_data,
        )
        == {}
    )

    # Existing plan/mode/git denials remain enforced even when project override is set
    models.set_override("claude", "planner", model="opus", scope="project", cwd=repo, home=paddock_home)
    for bad_input in (
        {"subagent_type": "paddock:implementer", "prompt": "Fix a.py without mode or plan"},
        {"subagent_type": "paddock:implementer", "prompt": "Mode: git push --tags"},
        {"subagent_type": "paddock:implementer", "prompt": "Run docs/plans/unapproved.md"},
        {"subagent_type": "paddock:planner", "prompt": "Plan docs/specs/f.md"},
    ):
        denied = _run_hook(
            "pre-tool-use",
            {"session_id": "s1", "cwd": str(repo), "tool_name": "Agent", "tool_input": bad_input},
            paddock_home,
            plugin_data,
        )
        assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_claude_hooks_corrupt_models_json_fails_open(tmp_path: Path) -> None:
    paddock_home = tmp_path / "paddock"
    plugin_data = tmp_path / "plugin-data"
    repo = tmp_path / "repo"
    repo.mkdir()

    global_cfg = models.config_path("global", repo, home=paddock_home)
    global_cfg.parent.mkdir(parents=True, exist_ok=True)
    global_cfg.write_text("{corrupt-json", encoding="utf-8")

    project_cfg = models.config_path("project", repo, home=paddock_home)
    project_cfg.parent.mkdir(parents=True, exist_ok=True)
    project_cfg.write_text('{"schema_version": 99}', encoding="utf-8")

    ss = _run_hook("session-start", {"cwd": str(repo)}, paddock_home, plugin_data)
    ss_ctx = ss["hookSpecificOutput"]["additionalContext"]
    assert "### Paddock Claude Models" in ss_ctx
    assert "generalist=haiku/medium" in ss_ctx
    assert "implementer=sonnet/medium" in ss_ctx
    assert "Project overrides differing from installed static frontmatter:" not in ss_ctx

    assert (
        _run_hook(
            "pre-tool-use",
            {
                "cwd": str(repo),
                "tool_name": "Agent",
                "tool_input": {"subagent_type": "implementer", "prompt": "Mode: quick fix a.py"},
            },
            paddock_home,
            plugin_data,
        )
        == {}
    )

    deny_git = _run_hook(
        "pre-tool-use",
        {
            "cwd": str(repo),
            "tool_name": "Agent",
            "tool_input": {"subagent_type": "implementer", "prompt": "Mode: git commit -m 'x'"},
        },
        paddock_home,
        plugin_data,
    )
    assert deny_git["hookSpecificOutput"]["permissionDecision"] == "deny"
