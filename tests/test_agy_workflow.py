"""Unit tests for Antigravity deterministic workflow platforms/antigravity/workflows/build_plan.py."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from core.memory import memory

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = REPO_ROOT / "platforms" / "antigravity" / "workflows" / "build_plan.py"


def _drive_coro(coro):
    try:
        coro.send(None)
    except StopIteration as exc:
        return exc.value
    raise AssertionError("Coroutine yielded unexpectedly without event loop I/O")


def _load_workflow_namespace(extra_globals: dict | None = None) -> dict:
    src = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "__file__" not in src, "build_plan.py must not depend on __file__"
    code = compile(src, "<workflow>", "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
    ns: dict = {}
    if extra_globals:
        ns.update(extra_globals)
    coro = eval(code, ns, ns)
    if inspect.iscoroutine(coro):
        _drive_coro(coro)
    return ns


def _write_sample_plan(path: Path) -> None:
    path.write_text(
        """# Sample Plan

### Task 1 — Implement Core
- **Agent:** paddock-implementer
- **Files:** core/app.py, tests/test_app.py
- **Scope:** Add feature X.
- **Acceptance:** `python -m pytest -q tests/test_app.py` passes.

### Task 2 — Update Docs
- **Agent:** generalist
- **Files:** README.md
- **Scope:** Document feature X.
- **Acceptance:** README mentions X.
""",
        encoding="utf-8",
    )


def test_parse_plan_and_happy_path_with_trap_injection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PADDOCK_HOME", str(tmp_path / "paddock"))
    repo = tmp_path / "repo"
    (repo / "core" / "memory").mkdir(parents=True)
    # Copy memory.py into temp repo so load_traps_for_task resolves cleanly
    (repo / "core" / "memory" / "memory.py").write_text(
        (REPO_ROOT / "core" / "memory" / "memory.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    memory.write_entry(
        repo,
        "project",
        "core-trap",
        "Watch core/app.py",
        {
            "type": "project",
            "trap": True,
            "paths": ["core/**"],
            "status": "active",
            "verified": "human",
            "verified_on": "2026-11-23",
            "stale_after": "2027-05-23",
        },
        "**Symptom:** Bug.\n**Cause:** C.\n**Do instead:** Fix.\n",
    )

    plan_file = repo / "plan.md"
    _write_sample_plan(plan_file)

    calls: list[dict] = []

    async def fake_agent(prompt=None, schema=None, role=None, type_name="self", workspace="inherit", model=None):
        calls.append({"type_name": type_name, "model": model, "workspace": workspace, "prompt": prompt})
        if type_name == "paddock-verifier":
            return {"verdict": "PASS", "summary": "Verified", "evidence": ["pytest: 1 passed"], "unverified": []}
        return {"status": "DONE", "summary": "Done", "evidence": ["edited"], "unverified": []}

    phases: list[str] = []
    ns = _load_workflow_namespace(
        {
            "agent": fake_agent,
            "phase": phases.append,
            "log": lambda *a: None,
            "WORKSPACES": (str(repo),),
            "args": {"plan_path": "plan.md"},
        }
    )

    summary = ns["_workflow_summary"]
    assert summary["status"] == "PASS"
    assert len(summary["tasks"]) == 2
    assert phases == ["Task 1 — Implement Core", "Task 2 — Update Docs"]
    assert calls[0]["type_name"] == "paddock-implementer"
    assert calls[0]["model"] == "flash"
    assert "core-trap" in calls[0]["prompt"]
    assert calls[2]["type_name"] == "paddock-generalist"
    assert calls[2]["model"] == "flash_lite"


def test_retry_caps_two_impl_to_plan_and_plan_cap_escalation(tmp_path: Path) -> None:
    ns = _load_workflow_namespace()
    plan_file = tmp_path / "plan.md"
    plan_file.write_text(
        "### Task 1 — Hard Task\n- **Agent:** implementer\n- **Files:** a.py\n- **Scope:** X\n- **Acceptance:** Y\n",
        encoding="utf-8",
    )

    seq: list[str] = []

    async def fake_agent(prompt=None, schema=None, role=None, type_name="self", workspace="inherit", model=None):
        seq.append(type_name)
        if type_name == "paddock-planner":
            return {"status": "DONE", "summary": "Revised", "evidence": [], "unverified": []}
        if type_name == "paddock-verifier":
            return {"verdict": "FAIL: impl", "summary": "Still broken", "evidence": [], "unverified": []}
        return {"status": "DONE", "summary": "Tried", "evidence": [], "unverified": []}

    res = _drive_coro(
        ns["run_build_loop"](
            plan_file,
            str(tmp_path),
            {},
            agent_fn=fake_agent,
            trap_loader=lambda *a: [],
        )
    )
    assert res["status"] == "ESCALATED: plan_cap"
    # 2x impl fail -> plan rev 1 -> 2x impl fail -> plan rev 2 -> 2x impl fail -> plan rev 3 hits cap
    assert seq.count("paddock-planner") == 2
    assert seq.count("paddock-verifier") == 6


def test_dispute_allowed_once_per_task_and_env_flaky_rerun(tmp_path: Path) -> None:
    ns = _load_workflow_namespace()
    plan_file = tmp_path / "plan.md"
    plan_file.write_text(
        "### Task 1 — Dispute & Env\n- **Agent:** implementer\n- **Files:** a.py\n- **Scope:** X\n- **Acceptance:** Y\n",
        encoding="utf-8",
    )

    # Case 1: First DISPUTE reaches verifier with dispute_evidence and PASSes
    seen_prompts: list[str] = []

    async def dispute_pass(prompt=None, schema=None, role=None, type_name="self", workspace="inherit", model=None):
        seen_prompts.append(prompt or "")
        if type_name == "paddock-implementer":
            return {
                "status": "DISPUTE",
                "summary": "Check spec",
                "evidence": ["line 10"],
                "unverified": [],
                "dispute_evidence": "Spec allows X",
            }
        return {"verdict": "PASS", "summary": "Accepted dispute", "evidence": ["line 10"], "unverified": []}

    res1 = _drive_coro(
        ns["run_build_loop"](plan_file, str(tmp_path), {}, agent_fn=dispute_pass, trap_loader=lambda *a: [])
    )
    assert res1["status"] == "PASS"
    assert res1["tasks"][0]["dispute_used"] is True
    assert "Spec allows X" in seen_prompts[1]

    # Case 2: Second DISPUTE counts as FAIL: impl -> 3rd attempt hits 2x impl -> FAIL: plan -> ESCALATED: plan_cap
    worker_calls = 0

    async def double_dispute(prompt=None, schema=None, role=None, type_name="self", workspace="inherit", model=None):
        nonlocal worker_calls
        if type_name == "paddock-planner":
            return {"status": "DONE", "summary": "Rev", "evidence": [], "unverified": []}
        if type_name == "paddock-verifier":
            return {"verdict": "FAIL: impl", "summary": "Rejected dispute", "evidence": [], "unverified": []}
        worker_calls += 1
        return {
            "status": "DISPUTE",
            "summary": "Dispute again",
            "evidence": [],
            "unverified": [],
            "dispute_evidence": "Again",
        }

    res2 = _drive_coro(
        ns["run_build_loop"](plan_file, str(tmp_path), {}, agent_fn=double_dispute, trap_loader=lambda *a: [])
    )
    assert res2["status"] == "ESCALATED: plan_cap"

    # Case 3: FAIL: env triggers one flaky rerun; PASS on rerun succeeds
    verdicts = iter(
        [
            {"verdict": "FAIL: env", "summary": "Flaky timeout", "evidence": [], "unverified": []},
            {"verdict": "PASS", "summary": "Passed on rerun", "evidence": ["pytest ok"], "unverified": []},
        ]
    )

    async def flaky_env(prompt=None, schema=None, role=None, type_name="self", workspace="inherit", model=None):
        if type_name == "paddock-verifier":
            return next(verdicts)
        return {"status": "DONE", "summary": "Done", "evidence": [], "unverified": []}

    res3 = _drive_coro(
        ns["run_build_loop"](plan_file, str(tmp_path), {}, agent_fn=flaky_env, trap_loader=lambda *a: [])
    )
    assert res3["status"] == "PASS"

    # Case 4: Persistent FAIL: env escalates; FAIL: spec stops immediately
    async def persistent_env(prompt=None, schema=None, role=None, type_name="self", workspace="inherit", model=None):
        if type_name == "paddock-verifier":
            return {"verdict": "FAIL: env", "summary": "Missing binary", "evidence": [], "unverified": []}
        return {"status": "DONE", "summary": "Done", "evidence": [], "unverified": []}

    res4 = _drive_coro(
        ns["run_build_loop"](plan_file, str(tmp_path), {}, agent_fn=persistent_env, trap_loader=lambda *a: [])
    )
    assert res4["status"] == "ESCALATED: env"

    async def fail_spec(prompt=None, schema=None, role=None, type_name="self", workspace="inherit", model=None):
        if type_name == "paddock-verifier":
            return {"verdict": "FAIL: spec", "summary": "Ambiguous auth", "evidence": [], "unverified": []}
        return {"status": "DONE", "summary": "Done", "evidence": [], "unverified": []}

    res5 = _drive_coro(
        ns["run_build_loop"](plan_file, str(tmp_path), {}, agent_fn=fail_spec, trap_loader=lambda *a: [])
    )
    assert res5["status"] == "ESCALATED: spec"
