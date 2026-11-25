"""Tests for platforms/antigravity/scripts/agy_hooks.py."""

from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from core.config import models
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
    assert allow_ov["overwrite"]["Model"] == "pro"

    # Second call with marker already present does not double-inject Prompt, still injects standalone Model alias
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
    assert no_dup == {"decision": "allow", "overwrite": {"Model": "pro"}}


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


def test_session_start_antigravity_model_summary_and_cap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    paddock_home = tmp_path / "paddock"
    monkeypatch.setenv("PADDOCK_HOME", str(paddock_home))
    repo = tmp_path / "repo"
    repo.mkdir()

    # Default balanced summary includes both swarm_model and subagent_alias
    ss_default = _run_hook("SessionStart", {"hookEventName": "SessionStart", "cwd": str(repo)}, paddock_home)
    msg_default = ss_default["injectSteps"][0]["systemMessage"]["systemMessage"]
    assert "Paddock Antigravity models (swarm / subagent_alias):" in msg_default
    assert "generalist=gemini-4-hydrogen-medium (flash)" in msg_default
    assert "implementer=gemini-4-neon-medium (pro)" in msg_default
    assert "verifier=gemini-4-neon-medium (pro)" in msg_default

    # Global budget preset + project anthropic provider and effort override reflected in summary
    models.set_preset("budget", scope="global", cwd=repo, home=paddock_home)
    models.set_provider("antigravity", "anthropic", scope="project", cwd=repo, home=paddock_home)
    models.set_role("implementer", "workhorse", effort="high", scope="project", cwd=repo, home=paddock_home)

    # Large memory index entry triggers 19,500-char cap while preserving the models summary line
    memory.write_entry(
        repo,
        "user",
        "huge-pref",
        "A" * 24000,
        {"type": "user", "status": "active", "verified": "human", "verified_on": "2026-11-24"},
        "Concise.\n",
    )

    ss_custom = _run_hook("SessionStart", {"hookEventName": "SessionStart", "cwd": str(repo)}, paddock_home)
    msg_custom = ss_custom["injectSteps"][0]["systemMessage"]["systemMessage"]
    assert len(msg_custom) <= agy_hooks.MAX_SESSION_MSG_CHARS
    assert "...<truncated>" in msg_custom
    assert "Paddock Antigravity models (swarm / subagent_alias):" in msg_custom
    assert "generalist=gemini-4-hydrogen-medium (flash)" in msg_custom
    assert "implementer=claude-sonnet-5-5-high (pro)" in msg_custom
    assert "planner=claude-sonnet-5-5-low (pro)" in msg_custom
    assert "verifier=gemini-4-hydrogen-high (flash)" in msg_custom


def test_invoke_subagent_model_alias_injection_skip_and_preservation(tmp_path: Path) -> None:
    paddock_home = tmp_path / "paddock"
    repo = tmp_path / "repo"
    repo.mkdir()
    prompt_marked = f"Mode: quick fix a.py\n\n{agy_hooks.INJECTED_MARKER}\n{agy_hooks.VERIFY_RULES}"

    # Balanced default: generalist -> flash, implementer ("inherit" or omitted) -> pro
    gen_omitted = _run_hook(
        "PreToolUse",
        {
            "hookEventName": "PreToolUse",
            "cwd": str(repo),
            "toolCall": {"name": "invoke_subagent", "args": {"TypeName": "paddock-generalist", "Prompt": "Mode: lookup find foo"}},
        },
        paddock_home,
    )
    assert gen_omitted["decision"] == "allow"
    assert gen_omitted["overwrite"]["Model"] == "flash"
    assert agy_hooks.INJECTED_MARKER in gen_omitted["overwrite"]["Prompt"]

    impl_inherit = _run_hook(
        "PreToolUse",
        {
            "hookEventName": "PreToolUse",
            "cwd": str(repo),
            "toolCall": {
                "name": "invoke_subagent",
                "args": {"TypeName": "paddock-implementer", "Prompt": prompt_marked, "Model": "inherit"},
            },
        },
        paddock_home,
    )
    assert impl_inherit == {"decision": "allow", "overwrite": {"Model": "pro"}}

    # Explicit caller Model values (e.g. "pro", "flash_lite") are preserved untouched
    gen_explicit = _run_hook(
        "PreToolUse",
        {
            "hookEventName": "PreToolUse",
            "cwd": str(repo),
            "toolCall": {
                "name": "invoke_subagent",
                "args": {"TypeName": "paddock-generalist", "Prompt": prompt_marked, "Model": "pro"},
            },
        },
        paddock_home,
    )
    assert gen_explicit == {"decision": "allow"}

    impl_explicit = _run_hook(
        "PreToolUse",
        {
            "hookEventName": "PreToolUse",
            "cwd": str(repo),
            "toolCall": {
                "name": "invoke_subagent",
                "args": {"TypeName": "paddock-implementer", "Prompt": "Mode: quick fix a.py", "Model": "flash_lite"},
            },
        },
        paddock_home,
    )
    assert impl_explicit["decision"] == "allow"
    assert "Prompt" in impl_explicit["overwrite"]
    assert "Model" not in impl_explicit["overwrite"]

    # Quality preset resolves implementer to flagship -> subagent_alias="inherit" (skip Model overwrite)
    models.set_preset("quality", scope="project", cwd=repo, home=paddock_home)
    impl_quality = _run_hook(
        "PreToolUse",
        {
            "hookEventName": "PreToolUse",
            "cwd": str(repo),
            "toolCall": {
                "name": "invoke_subagent",
                "args": {"TypeName": "paddock-implementer", "Prompt": prompt_marked},
            },
        },
        paddock_home,
    )
    assert impl_quality == {"decision": "allow"}

    ui_quality = _run_hook(
        "PreToolUse",
        {
            "hookEventName": "PreToolUse",
            "cwd": str(repo),
            "toolCall": {
                "name": "invoke_subagent",
                "args": {"TypeName": "paddock-ui", "Prompt": prompt_marked, "Model": "inherit"},
            },
        },
        paddock_home,
    )
    assert ui_quality == {"decision": "allow", "overwrite": {"Model": "pro"}}

    # Explicit flash_lite override in models.json is injected verbatim
    models.set_override("antigravity", "generalist", model="flash_lite", scope="project", cwd=repo, home=paddock_home)
    gen_override = _run_hook(
        "PreToolUse",
        {
            "hookEventName": "PreToolUse",
            "cwd": str(repo),
            "toolCall": {
                "name": "invoke_subagent",
                "args": {"TypeName": "paddock-generalist", "Prompt": prompt_marked},
            },
        },
        paddock_home,
    )
    assert gen_override == {"decision": "allow", "overwrite": {"Model": "flash_lite"}}


def test_swarm_add_model_injection_gates_traps_and_preservation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    paddock_home = tmp_path / "paddock"
    monkeypatch.setenv("PADDOCK_HOME", str(paddock_home))
    repo = tmp_path / "repo"
    (repo / "docs" / "specs").mkdir(parents=True)
    (repo / "docs" / "plans").mkdir(parents=True)

    memory.write_entry(
        repo,
        "project",
        "swarm-src-trap",
        "Watch src/app.py",
        {
            "type": "project",
            "trap": True,
            "paths": ["src/**"],
            "status": "active",
            "verified": "human",
            "verified_on": "2026-11-24",
            "stale_after": "2027-05-24",
        },
        "**Symptom:** Race.\n**Cause:** Lock.\n**Do instead:** Serialize.\n",
    )

    spec_file = repo / "docs" / "specs" / "feat.md"
    plan_file = repo / "docs" / "plans" / "feat.md"
    spec_file.write_text("# Spec\n", encoding="utf-8")
    plan_file.write_text("### Task 1\n- Agent: paddock-implementer\n- Files: src/app.py\n", encoding="utf-8")

    t_user = datetime.datetime(2026, 11, 24, 10, 0, 0, tzinfo=datetime.timezone.utc)
    t_future = t_user.timestamp() + 60.0
    os.utime(spec_file, (t_future, t_future))
    os.utime(plan_file, (t_future, t_future))
    transcript = tmp_path / "transcript.jsonl"
    _write_transcript(
        transcript,
        [{"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "created_at": t_user.isoformat().replace("+00:00", "Z"), "content": "check"}],
    )

    # Non-add actions and non-Paddock worker names are ignored cleanly
    for action, name in (("list", "implementer"), ("status", "paddock-ui"), ("add", "helper"), ("add", "builder")):
        ignored = _run_hook(
            "PreToolUse",
            {
                "hookEventName": "PreToolUse",
                "cwd": str(repo),
                "transcriptPath": str(transcript),
                "toolCall": {"name": "swarm", "args": {"action": action, "name": name, "brief": "hello"}},
            },
            paddock_home,
        )
        assert ignored == {"decision": "allow"}

    # plan-gate and Mode: git checks apply to swarm action="add" on Paddock workers
    deny_missing = _run_hook(
        "PreToolUse",
        {
            "hookEventName": "PreToolUse",
            "cwd": str(repo),
            "transcriptPath": str(transcript),
            "toolCall": {"name": "swarm", "args": {"action": "add", "name": "implementer-1", "brief": "fix src/app.py"}},
        },
        paddock_home,
    )
    assert deny_missing["decision"] == "deny" and "plan-gate" in deny_missing["reason"]

    deny_plan_gate = _run_hook(
        "PreToolUse",
        {
            "hookEventName": "PreToolUse",
            "cwd": str(repo),
            "transcriptPath": str(transcript),
            "toolCall": {"name": "swarm", "args": {"action": "add", "name": "paddock-implementer", "brief": "Run docs/plans/feat.md"}},
        },
        paddock_home,
    )
    assert deny_plan_gate["decision"] == "deny" and "Approval Gate 2" in deny_plan_gate["reason"]

    deny_git = _run_hook(
        "PreToolUse",
        {
            "hookEventName": "PreToolUse",
            "cwd": str(repo),
            "transcriptPath": str(transcript),
            "toolCall": {"name": "swarm", "args": {"action": "add", "name": "implementer", "brief": "Mode: git commit -m 'bad'"}},
        },
        paddock_home,
    )
    assert deny_git["decision"] == "deny" and "Mode: git" in deny_git["reason"]

    # Balanced default: VERIFY_RULES + matched trap injected into brief and exact swarm_model set
    bal_gen = _run_hook(
        "PreToolUse",
        {
            "hookEventName": "PreToolUse",
            "cwd": str(repo),
            "transcriptPath": str(transcript),
            "toolCall": {"name": "swarm", "args": {"action": "add", "name": "generalist", "brief": "Mode: lookup read src/app.py"}},
        },
        paddock_home,
    )
    assert bal_gen["decision"] == "allow"
    assert bal_gen["overwrite"]["model"] == "gemini-4-hydrogen-medium"
    assert agy_hooks.INJECTED_MARKER in bal_gen["overwrite"]["brief"]
    assert agy_hooks.VERIFY_RULES in bal_gen["overwrite"]["brief"]
    assert "swarm-src-trap" in bal_gen["overwrite"]["brief"]

    bal_ui = _run_hook(
        "PreToolUse",
        {
            "hookEventName": "PreToolUse",
            "cwd": str(repo),
            "transcriptPath": str(transcript),
            "toolCall": {"name": "swarm", "args": {"action": "add", "name": "paddock-ui-2", "brief": "Mode: quick edit src/app.py"}},
        },
        paddock_home,
    )
    assert bal_ui["overwrite"]["model"] == "gemini-4-neon-medium"

    # Explicit caller model is preserved untouched
    explicit_swarm = _run_hook(
        "PreToolUse",
        {
            "hookEventName": "PreToolUse",
            "cwd": str(repo),
            "transcriptPath": str(transcript),
            "toolCall": {
                "name": "swarm",
                "args": {
                    "action": "add",
                    "name": "implementer",
                    "model": "claude-opus-5-5-high",
                    "brief": bal_gen["overwrite"]["brief"],
                },
            },
        },
        paddock_home,
    )
    assert explicit_swarm == {"decision": "allow"}

    explicit_with_brief = _run_hook(
        "PreToolUse",
        {
            "hookEventName": "PreToolUse",
            "cwd": str(repo),
            "transcriptPath": str(transcript),
            "toolCall": {
                "name": "swarm",
                "args": {"action": "add", "name": "implementer", "model": "gemini-4-carbon-low", "brief": "Mode: quick fix src/app.py"},
            },
        },
        paddock_home,
    )
    assert "brief" in explicit_with_brief["overwrite"]
    assert "model" not in explicit_with_brief["overwrite"]

    # Budget preset: implementer/verifier -> gemini-4-hydrogen-high, ui -> gemini-4-neon-low
    models.set_preset("budget", scope="global", cwd=repo, home=paddock_home)
    for worker, expected in (
        ("implementer", "gemini-4-hydrogen-high"),
        ("verifier-1", "gemini-4-hydrogen-high"),
        ("ui", "gemini-4-neon-low"),
    ):
        res = _run_hook(
            "PreToolUse",
            {
                "hookEventName": "PreToolUse",
                "cwd": str(repo),
                "transcriptPath": str(transcript),
                "toolCall": {"name": "swarm", "args": {"action": "add", "name": worker, "brief": f"Mode: quick {worker}"}},
            },
            paddock_home,
        )
        assert res["overwrite"]["model"] == expected

    # Quality preset (Google Gemini 4): implementer -> gemini-4-carbon-medium, ui -> gemini-4-neon-high
    models.set_preset("quality", scope="project", cwd=repo, home=paddock_home)
    qual_impl = _run_hook(
        "PreToolUse",
        {
            "hookEventName": "PreToolUse",
            "cwd": str(repo),
            "transcriptPath": str(transcript),
            "toolCall": {"name": "swarm", "args": {"action": "add", "name": "implementer", "brief": "Mode: quick run"}},
        },
        paddock_home,
    )
    assert qual_impl["overwrite"]["model"] == "gemini-4-carbon-medium"

    qual_ui = _run_hook(
        "PreToolUse",
        {
            "hookEventName": "PreToolUse",
            "cwd": str(repo),
            "transcriptPath": str(transcript),
            "toolCall": {"name": "swarm", "args": {"action": "add", "name": "ui", "brief": "Mode: quick style"}},
        },
        paddock_home,
    )
    assert qual_ui["overwrite"]["model"] == "gemini-4-neon-high"

    # Anthropic provider overlay on quality preset: claude-opus-5-5-medium & claude-sonnet-5-5-high
    models.set_provider("antigravity", "anthropic", scope="project", cwd=repo, home=paddock_home)
    anth_impl = _run_hook(
        "PreToolUse",
        {
            "hookEventName": "PreToolUse",
            "cwd": str(repo),
            "transcriptPath": str(transcript),
            "toolCall": {"name": "swarm", "args": {"action": "add", "name": "implementer", "brief": "Mode: quick run"}},
        },
        paddock_home,
    )
    assert anth_impl["overwrite"]["model"] == "claude-opus-5-5-medium"

    anth_ui = _run_hook(
        "PreToolUse",
        {
            "hookEventName": "PreToolUse",
            "cwd": str(repo),
            "transcriptPath": str(transcript),
            "toolCall": {"name": "swarm", "args": {"action": "add", "name": "ui", "brief": "Mode: quick style"}},
        },
        paddock_home,
    )
    assert anth_ui["overwrite"]["model"] == "claude-sonnet-5-5-high"


def test_corrupt_models_json_fails_open_to_balanced(tmp_path: Path) -> None:
    paddock_home = tmp_path / "paddock"
    repo = tmp_path / "repo"
    repo.mkdir()

    global_cfg = models.config_path("global", repo, home=paddock_home)
    global_cfg.parent.mkdir(parents=True, exist_ok=True)
    global_cfg.write_text("{corrupt-json", encoding="utf-8")

    project_cfg = models.config_path("project", repo, home=paddock_home)
    project_cfg.parent.mkdir(parents=True, exist_ok=True)
    project_cfg.write_text('{"schema_version": 99}', encoding="utf-8")

    ss = _run_hook("SessionStart", {"hookEventName": "SessionStart", "cwd": str(repo)}, paddock_home)
    msg = ss["injectSteps"][0]["systemMessage"]["systemMessage"]
    assert "generalist=gemini-4-hydrogen-medium (flash)" in msg
    assert "implementer=gemini-4-neon-medium (pro)" in msg

    sub_gen = _run_hook(
        "PreToolUse",
        {
            "hookEventName": "PreToolUse",
            "cwd": str(repo),
            "toolCall": {"name": "invoke_subagent", "args": {"TypeName": "paddock-generalist", "Prompt": "Mode: lookup x"}},
        },
        paddock_home,
    )
    assert sub_gen["overwrite"]["Model"] == "flash"

    swarm_impl = _run_hook(
        "PreToolUse",
        {
            "hookEventName": "PreToolUse",
            "cwd": str(repo),
            "toolCall": {"name": "swarm", "args": {"action": "add", "name": "implementer", "brief": "Mode: quick fix"}},
        },
        paddock_home,
    )
    assert swarm_impl["overwrite"]["model"] == "gemini-4-neon-medium"


def test_agy_hooks_json_includes_call_mcp_tool_matcher() -> None:
    hooks_cfg = json.loads(
        (REPO_ROOT / "platforms" / "antigravity" / "hooks.json").read_text(encoding="utf-8")
    )
    pre_entries = hooks_cfg["paddock"]["PreToolUse"]
    matched = [entry for entry in pre_entries if entry.get("matcher") == "call_mcp_tool"]
    assert len(matched) == 1
    hook = matched[0]["hooks"][0]
    assert "agy_hooks.py PreToolUse" in hook["command"]
    assert hook["timeout"] == 15


def test_call_mcp_tool_worktree_routing_gates_and_ask(tmp_path: Path) -> None:
    paddock_home = tmp_path / "paddock"
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    wt = repo / ".worktrees" / "wt-a"
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "-b", "feat/wt-a", str(wt)],
        check=True,
        capture_output=True,
    )

    plans_dir = repo / "docs" / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)

    t_user = datetime.datetime(2026, 11, 25, 10, 0, 0, tzinfo=datetime.timezone.utc)
    t_past = t_user.timestamp() - 60.0
    t_future = t_user.timestamp() + 60.0

    approved_plan = plans_dir / "approved.md"
    approved_plan.write_text("### Task 1\n- Agent: implementer\n- Files: a.py\n", encoding="utf-8")
    os.utime(approved_plan, (t_past, t_past))

    unapproved_plan = plans_dir / "unapproved.md"
    unapproved_plan.write_text("### Task 1\n- Agent: implementer\n- Files: a.py\n", encoding="utf-8")
    os.utime(unapproved_plan, (t_future, t_future))

    malformed_plan = plans_dir / "malformed.md"
    malformed_plan.write_text("### Task 1\nMissing required fields.\n", encoding="utf-8")
    os.utime(malformed_plan, (t_past, t_past))

    transcript = tmp_path / "transcript.jsonl"
    _write_transcript(
        transcript,
        [
            {
                "step_index": 0,
                "source": "USER_EXPLICIT",
                "type": "USER_INPUT",
                "created_at": t_user.isoformat().replace("+00:00", "Z"),
                "content": "approved",
            }
        ],
    )

    for server_name in ("paddock_paddock-worktree", "paddock-worktree"):
        # worktree_list -> allow
        assert (
            _run_hook(
                "PreToolUse",
                {
                    "hookEventName": "PreToolUse",
                    "cwd": str(repo),
                    "transcriptPath": str(transcript),
                    "toolCall": {
                        "name": "call_mcp_tool",
                        "args": {"ServerName": server_name, "ToolName": "worktree_list", "Arguments": {"cwd": str(repo)}},
                    },
                },
                paddock_home,
            )
            == {"decision": "allow"}
        )

        # worktree_create without plan_path -> allow
        for extra_args in ({}, {"plan_path": ""}):
            assert (
                _run_hook(
                    "PreToolUse",
                    {
                        "hookEventName": "PreToolUse",
                        "cwd": str(repo),
                        "transcriptPath": str(transcript),
                        "toolCall": {
                            "name": "call_mcp_tool",
                            "args": {
                                "ServerName": server_name,
                                "ToolName": "worktree_create",
                                "Arguments": {"cwd": str(repo), "slug": "feat-x", **extra_args},
                            },
                        },
                    },
                    paddock_home,
                )
                == {"decision": "allow"}
            )

        # worktree_create missing / malformed / unapproved plan_path -> deny
        deny_missing = _run_hook(
            "PreToolUse",
            {
                "hookEventName": "PreToolUse",
                "cwd": str(repo),
                "transcriptPath": str(transcript),
                "toolCall": {
                    "name": "call_mcp_tool",
                    "args": {
                        "ServerName": server_name,
                        "ToolName": "worktree_create",
                        "Arguments": {"cwd": str(repo), "slug": "feat-x", "plan_path": "docs/plans/missing.md"},
                    },
                },
            },
            paddock_home,
        )
        assert deny_missing["decision"] == "deny" and "does not exist" in deny_missing["reason"]

        deny_malformed = _run_hook(
            "PreToolUse",
            {
                "hookEventName": "PreToolUse",
                "cwd": str(repo),
                "transcriptPath": str(transcript),
                "toolCall": {
                    "name": "call_mcp_tool",
                    "args": {
                        "ServerName": server_name,
                        "ToolName": "worktree_create",
                        "Arguments": {"cwd": str(repo), "slug": "feat-x", "plan_path": "docs/plans/malformed.md"},
                    },
                },
            },
            paddock_home,
        )
        assert deny_malformed["decision"] == "deny" and "Agent:" in deny_malformed["reason"]

        deny_unapproved = _run_hook(
            "PreToolUse",
            {
                "hookEventName": "PreToolUse",
                "cwd": str(repo),
                "transcriptPath": str(transcript),
                "toolCall": {
                    "name": "call_mcp_tool",
                    "args": {
                        "ServerName": server_name,
                        "ToolName": "worktree_create",
                        "Arguments": {"cwd": str(repo), "slug": "feat-x", "plan_path": "docs/plans/unapproved.md"},
                    },
                },
            },
            paddock_home,
        )
        assert deny_unapproved["decision"] == "deny" and "Approval Gate 2" in deny_unapproved["reason"]

        # worktree_create with approved relative plan_path resolved via Arguments.cwd (payload.cwd differs) -> allow
        assert (
            _run_hook(
                "PreToolUse",
                {
                    "hookEventName": "PreToolUse",
                    "cwd": str(tmp_path),
                    "transcriptPath": str(transcript),
                    "toolCall": {
                        "name": "call_mcp_tool",
                        "args": {
                            "ServerName": server_name,
                            "ToolName": "worktree_create",
                            "Arguments": {"cwd": str(repo), "slug": "feat-x", "plan_path": "docs/plans/approved.md"},
                        },
                    },
                },
                paddock_home,
            )
            == {"decision": "allow"}
        )

        # worktree_remove -> ask with exact target & delete_branch boolean
        ask_default = _run_hook(
            "PreToolUse",
            {
                "hookEventName": "PreToolUse",
                "cwd": str(repo),
                "transcriptPath": str(transcript),
                "toolCall": {
                    "name": "call_mcp_tool",
                    "args": {
                        "ServerName": server_name,
                        "ToolName": "worktree_remove",
                        "Arguments": {"cwd": str(repo), "slug": "wt-a"},
                    },
                },
            },
            paddock_home,
        )
        assert ask_default == {
            "decision": "ask",
            "reason": "Confirm removing worktree '.worktrees/wt-a' (delete_branch=False). Non-force only.",
        }

        ask_del = _run_hook(
            "PreToolUse",
            {
                "hookEventName": "PreToolUse",
                "cwd": str(repo),
                "transcriptPath": str(transcript),
                "toolCall": {
                    "name": "call_mcp_tool",
                    "args": {
                        "ServerName": server_name,
                        "ToolName": "worktree_remove",
                        "Arguments": {"cwd": str(repo), "slug": "wt-a", "delete_branch": True},
                    },
                },
            },
            paddock_home,
        )
        assert ask_del == {
            "decision": "ask",
            "reason": "Confirm removing worktree '.worktrees/wt-a' (delete_branch=True). Non-force only.",
        }

    # server_name lowercase alias + repo_root fallback when payload.cwd is worktree and Arguments.cwd omitted
    assert (
        _run_hook(
            "PreToolUse",
            {
                "hookEventName": "PreToolUse",
                "cwd": str(wt),
                "transcriptPath": str(transcript),
                "toolCall": {
                    "name": "call_mcp_tool",
                    "args": {
                        "server_name": "paddock-worktree",
                        "ToolName": "worktree_create",
                        "Arguments": {"slug": "wt-b", "plan_path": "docs/plans/approved.md"},
                    },
                },
            },
            paddock_home,
        )
        == {"decision": "allow"}
    )

    # Unrelated MCP server -> allow
    assert (
        _run_hook(
            "PreToolUse",
            {
                "hookEventName": "PreToolUse",
                "cwd": str(repo),
                "transcriptPath": str(transcript),
                "toolCall": {
                    "name": "call_mcp_tool",
                    "args": {
                        "ServerName": "paddock_paddock-mem",
                        "ToolName": "mem_write",
                        "Arguments": {"cwd": str(repo)},
                    },
                },
            },
            paddock_home,
        )
        == {"decision": "allow"}
    )


def test_run_workflow_worktree_plan_resolution_priority_and_fallback(tmp_path: Path) -> None:
    paddock_home = tmp_path / "paddock"
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    wt = repo / ".worktrees" / "wt-plan"
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "-b", "feat/wt-plan", str(wt)],
        check=True,
        capture_output=True,
    )
    wt_sub = wt / "sub"
    wt_sub.mkdir(parents=True, exist_ok=True)

    t_user = datetime.datetime(2026, 11, 25, 11, 0, 0, tzinfo=datetime.timezone.utc)
    t_past = t_user.timestamp() - 60.0
    t_future = t_user.timestamp() + 60.0

    transcript = tmp_path / "transcript.jsonl"
    _write_transcript(
        transcript,
        [
            {
                "step_index": 0,
                "source": "USER_EXPLICIT",
                "type": "USER_INPUT",
                "created_at": t_user.isoformat().replace("+00:00", "Z"),
                "content": "go",
            }
        ],
    )

    # Both repo and worktree have docs/plans/feat.md; repo is approved, worktree copy is unapproved -> worktree wins
    repo_plan = repo / "docs" / "plans" / "feat.md"
    wt_plan = wt / "docs" / "plans" / "feat.md"
    repo_plan.parent.mkdir(parents=True, exist_ok=True)
    wt_plan.parent.mkdir(parents=True, exist_ok=True)
    repo_plan.write_text("### Task 1\n- Agent: implementer\n- Files: a.py\n", encoding="utf-8")
    wt_plan.write_text("### Task 1\n- Agent: implementer\n- Files: a.py\n", encoding="utf-8")
    os.utime(repo_plan, (t_past, t_past))
    os.utime(wt_plan, (t_future, t_future))

    for wt_arg in (".worktrees/wt-plan", str(wt)):
        deny_wt = _run_hook(
            "PreToolUse",
            {
                "hookEventName": "PreToolUse",
                "cwd": str(repo),
                "transcriptPath": str(transcript),
                "toolCall": {
                    "name": "run_workflow",
                    "args": {
                        "ScriptPath": "workflows/build_plan.py",
                        "Args": {"worktree": wt_arg, "plan_path": "docs/plans/feat.md"},
                    },
                },
            },
            paddock_home,
        )
        assert deny_wt["decision"] == "deny" and "Approval Gate 2" in deny_wt["reason"]

    # Also verify _resolve_doc_ref branch checks worktree first when Args.plan_path is omitted
    deny_ref = _run_hook(
        "PreToolUse",
        {
            "hookEventName": "PreToolUse",
            "cwd": str(repo),
            "transcriptPath": str(transcript),
            "toolCall": {
                "name": "run_workflow",
                "args": {
                    "ScriptPath": "workflows/build_plan.py",
                    "Script": "Build docs/plans/feat.md",
                    "Args": {"worktree": ".worktrees/wt-plan"},
                },
            },
        },
        paddock_home,
    )
    assert deny_ref["decision"] == "deny" and "Approval Gate 2" in deny_ref["reason"]

    # Once active worktree copy is approved, both relative and absolute Args.worktree succeed
    os.utime(wt_plan, (t_past, t_past))
    for wt_arg in (".worktrees/wt-plan", str(wt)):
        assert (
            _run_hook(
                "PreToolUse",
                {
                    "hookEventName": "PreToolUse",
                    "cwd": str(repo),
                    "transcriptPath": str(transcript),
                    "toolCall": {
                        "name": "run_workflow",
                        "args": {
                            "ScriptPath": "workflows/build_plan.py",
                            "Args": {"worktree": wt_arg, "plan_path": "docs/plans/feat.md"},
                        },
                    },
                },
                paddock_home,
            )
            == {"decision": "allow"}
        )

    # Plan absent from worktree falls back cleanly to parent repo cwd / repo_root
    parent_only = repo / "docs" / "plans" / "parent-only.md"
    parent_only.write_text("### Task 1\n- Agent: implementer\n- Files: b.py\n", encoding="utf-8")
    os.utime(parent_only, (t_future, t_future))

    deny_fallback = _run_hook(
        "PreToolUse",
        {
            "hookEventName": "PreToolUse",
            "cwd": str(wt_sub),
            "transcriptPath": str(transcript),
            "toolCall": {
                "name": "run_workflow",
                "args": {
                    "ScriptPath": "workflows/build_plan.py",
                    "Args": {"worktree": str(wt), "plan_path": "docs/plans/parent-only.md"},
                },
            },
        },
        paddock_home,
    )
    assert deny_fallback["decision"] == "deny" and "Approval Gate 2" in deny_fallback["reason"]

    os.utime(parent_only, (t_past, t_past))
    assert (
        _run_hook(
            "PreToolUse",
            {
                "hookEventName": "PreToolUse",
                "cwd": str(wt_sub),
                "transcriptPath": str(transcript),
                "toolCall": {
                    "name": "run_workflow",
                    "args": {
                        "ScriptPath": "workflows/build_plan.py",
                        "Args": {"worktree": str(wt), "plan_path": "docs/plans/parent-only.md"},
                    },
                },
            },
            paddock_home,
        )
        == {"decision": "allow"}
    )
