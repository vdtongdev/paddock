"""Deterministic Antigravity `run_workflow` builder replacing LLM builder middleware."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import re
from typing import Any, Callable

WORKER_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {
            "type": "string",
            "enum": ["DONE", "BLOCKED: plan", "BLOCKED: spec", "BLOCKED: env", "DISPUTE"],
        },
        "summary": {"type": "string"},
        "evidence": {"type": "array", "items": {"type": "string"}},
        "unverified": {"type": "array", "items": {"type": "string"}},
        "dispute_evidence": {"type": "string"},
    },
    "required": ["status", "summary", "evidence", "unverified"],
}

VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {
            "type": "string",
            "enum": ["PASS", "FAIL: impl", "FAIL: plan", "FAIL: spec", "FAIL: env"],
        },
        "summary": {"type": "string"},
        "evidence": {"type": "array", "items": {"type": "string"}},
        "unverified": {"type": "array", "items": {"type": "string"}},
        "reason": {"type": "string"},
    },
    "required": ["verdict", "summary", "evidence", "unverified"],
}

DEFAULT_MODELS: dict[str, str] = {
    "paddock-generalist": "flash",
    "paddock-implementer": "pro",
    "paddock-ui": "pro",
    "paddock-planner": "pro",
    "paddock-verifier": "pro",
}

ROLE_MAP: dict[str, str] = {
    "generalist": "paddock-generalist",
    "paddock-generalist": "paddock-generalist",
    "implementer": "paddock-implementer",
    "paddock-implementer": "paddock-implementer",
    "ui": "paddock-ui",
    "paddock-ui": "paddock-ui",
    "planner": "paddock-planner",
    "paddock-planner": "paddock-planner",
    "verifier": "paddock-verifier",
    "paddock-verifier": "paddock-verifier",
}

TASK_HEADING_RE = re.compile(r"(?m)^#{2,3}\s+(Task\s+\S+.*)$")
FIELD_RE = re.compile(r"^\s*(?:[-*]\s*)?(?:\*\*)?(Agent|Files|Scope|Acceptance):(?:\*\*)?\s*(.*)$")


def normalize_agent_name(raw_agent: str) -> str:
    token = raw_agent.strip().strip("`").split()[0].lower()
    return ROLE_MAP.get(token, "paddock-implementer")


def parse_files_list(raw_files: str) -> list[str]:
    parts: list[str] = []
    for item in raw_files.split(","):
        cleaned = item.strip().strip("`").strip()
        if cleaned:
            parts.append(cleaned)
    return parts


def parse_plan(text: str) -> list[dict[str, Any]]:
    """Parse Task sections (`Agent:`, `Files:`, `Scope:`, `Acceptance:`) from plan markdown."""
    matches = list(TASK_HEADING_RE.finditer(text))
    tasks: list[dict[str, Any]] = []
    for idx, m in enumerate(matches):
        start = m.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        block = text[start:end].strip()
        title = m.group(1).strip()

        fields: dict[str, str] = {}
        current_key: str | None = None
        for line in block.splitlines():
            fm = FIELD_RE.match(line)
            if fm:
                current_key = fm.group(1)
                fields[current_key] = fm.group(2).strip()
            elif current_key and line.strip():
                fields[current_key] = (fields[current_key] + " " + line.strip()).strip()

        if "Agent" not in fields or "Files" not in fields:
            continue

        tasks.append(
            {
                "id": f"task-{len(tasks) + 1}",
                "title": title,
                "agent": normalize_agent_name(fields["Agent"]),
                "files": parse_files_list(fields["Files"]),
                "scope": fields.get("Scope", ""),
                "acceptance": fields.get("Acceptance", ""),
                "raw_block": block,
            }
        )
    return tasks


def _candidate_paddock_files(
    cwd: str, args_dict: dict[str, Any], repo_rel: Path, core_filename: str
) -> list[Path]:
    candidates: list[Path] = []
    plugin_root = args_dict.get("plugin_root")
    if isinstance(plugin_root, str) and plugin_root.strip():
        candidates.append(Path(plugin_root) / "scripts" / "paddock_core" / core_filename)
    candidates.append(Path(cwd) / repo_rel / core_filename)
    candidates.append(
        Path.home() / ".gemini" / "config" / "plugins" / "paddock" / "scripts" / "paddock_core" / core_filename
    )
    return candidates


def load_traps_for_task(cwd: str, files: list[str], args_dict: dict[str, Any]) -> list[dict[str, Any]]:
    """Load matching trap entries via memory.py using explicit workspace/plugin roots."""
    for cand in _candidate_paddock_files(cwd, args_dict, Path("core") / "memory", "memory.py"):
        if cand.is_file():
            try:
                spec = importlib.util.spec_from_file_location("paddock_workflow_memory", cand)
                if spec and spec.loader:
                    mod = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)
                    sel = mod.select(cwd, paths=files, traps_only=True)
                    return list(sel.get("entries", []))
            except Exception:
                return []
    return []


def load_models_for_workflow(cwd: str, args_dict: dict[str, Any]) -> dict[str, str]:
    """Load `models.py` and resolve Antigravity `subagent_alias` per role, failing open to DEFAULT_MODELS."""
    models = dict(DEFAULT_MODELS)
    for cand in _candidate_paddock_files(cwd, args_dict, Path("core") / "config", "models.py"):
        if not cand.is_file():
            continue
        try:
            spec = importlib.util.spec_from_file_location("paddock_workflow_models", cand)
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                resolved = mod.resolve_platform("antigravity", cwd)
                if isinstance(resolved, dict):
                    for role in ("generalist", "implementer", "ui", "planner", "verifier"):
                        entry = resolved.get(role)
                        if not isinstance(entry, dict):
                            continue
                        alias = entry.get("subagent_alias")
                        if not isinstance(alias, str) or not alias:
                            alias = mod.antigravity_subagent_alias(
                                entry.get("model"),
                                tier=entry.get("tier"),
                            )
                        if isinstance(alias, str) and alias:
                            models[f"paddock-{role}"] = alias
                    return models
        except Exception:
            continue
    return models


def _resolve_build_models(
    cwd: str,
    args_dict: dict[str, Any],
    model_loader: Callable[[str, dict[str, Any]], dict[str, str]],
) -> dict[str, str]:
    models = dict(DEFAULT_MODELS)
    loaded = model_loader(cwd, args_dict)
    if isinstance(loaded, dict):
        for raw_key, raw_val in loaded.items():
            if isinstance(raw_val, str) and raw_val.strip():
                key_str = str(raw_key).strip()
                models[ROLE_MAP.get(key_str.lower(), key_str)] = raw_val.strip()
    if isinstance(args_dict.get("models"), dict):
        for raw_key, raw_val in args_dict["models"].items():
            if raw_val is None or not str(raw_val).strip():
                continue
            key_str = str(raw_key).strip()
            models[ROLE_MAP.get(key_str.lower(), key_str)] = str(raw_val).strip()
    return models


def _format_traps_text(traps: list[dict[str, Any]]) -> str:
    if not traps:
        return "None."
    lines = []
    for t in traps[:4]:
        lines.append(f"- [[{t['name']}]]: {t['description']}\n  {t.get('body', '').strip()[:500]}")
    return "\n".join(lines)


def _worker_prompt(
    task: dict[str, Any],
    cwd: str,
    traps_text: str,
    feedback: str | None = None,
) -> str:
    fb = f"\n\nPrevious feedback to address:\n{feedback}" if feedback else ""
    return (
        f"Execute {task['title']} inside workspace `{cwd}`.\n"
        f"Files: {', '.join(task['files'])}\n"
        f"Scope: {task['scope']}\n"
        f"Acceptance: {task['acceptance']}\n\n"
        f"Relevant traps:\n{traps_text}{fb}\n\n"
        "Implement strictly within scope, run checks after final edit, and submit via `finish`."
    )


def _verifier_prompt(
    task: dict[str, Any],
    cwd: str,
    worker_res: dict[str, Any],
    dispute_note: str | None = None,
) -> str:
    disp = f"\n\nWorker DISPUTE evidence:\n{dispute_note}" if dispute_note else ""
    return (
        f"Verify {task['title']} inside workspace `{cwd}` (read-only; never edit files).\n"
        f"Files: {', '.join(task['files'])}\n"
        f"Acceptance: {task['acceptance']}\n\n"
        f"Worker reported ({worker_res.get('status')}): {worker_res.get('summary')}\n"
        f"Worker evidence: {json.dumps(worker_res.get('evidence', []))}{disp}\n\n"
        "Independently verify files and acceptance checks, then emit verdict via `finish`."
    )


def _planner_prompt(plan_path: Path, task: dict[str, Any], reason: str) -> str:
    return (
        f"Revise ONLY `{task['title']}` in plan `{plan_path}` due to: {reason}\n"
        "Preserve all unaffected tasks verbatim and report via `finish`."
    )


async def run_build_loop(
    plan_path: Path,
    cwd: str,
    args_dict: dict[str, Any],
    *,
    agent_fn: Callable[..., Any],
    phase_fn: Callable[[str], Any] | None = None,
    log_fn: Callable[..., Any] | None = None,
    trap_loader: Callable[[str, list[str], dict[str, Any]], list[dict[str, Any]]] = load_traps_for_task,
    model_loader: Callable[[str, dict[str, Any]], dict[str, str]] = load_models_for_workflow,
) -> dict[str, Any]:
    """Execute an approved plan with deterministic routing, retry caps, and DISPUTE limits."""
    models = _resolve_build_models(cwd, args_dict, model_loader)
    workspace_mode = str(args_dict.get("workspace") or "inherit")

    plan_text = plan_path.read_text(encoding="utf-8")
    tasks = parse_plan(plan_text)
    if not tasks:
        return {"status": "FAILED: empty_plan", "tasks": [], "plan_path": str(plan_path)}

    completed: list[dict[str, Any]] = []
    idx = 0
    while idx < len(tasks):
        task = tasks[idx]
        if phase_fn:
            phase_fn(task["title"])

        impl_fails = 0
        plan_revisions = 0
        dispute_used = False
        env_flaky_retried = False
        feedback: str | None = None

        async def _revise_plan(reason: str) -> str | None:
            nonlocal impl_fails, plan_revisions, feedback, tasks, task
            plan_revisions += 1
            if plan_revisions > 2:
                return f"Exceeded max plan revisions (2) for {task['title']}: {reason}"
            if log_fn:
                log_fn(f"Revision {plan_revisions}/2 for {task['title']}: {reason}")
            await agent_fn(
                prompt=_planner_prompt(plan_path, task, reason),
                schema=WORKER_SCHEMA,
                role=f"Revise {task['title']}",
                type_name="paddock-planner",
                workspace=workspace_mode,
                model=models.get("paddock-planner", DEFAULT_MODELS["paddock-planner"]),
            )
            refreshed = parse_plan(plan_path.read_text(encoding="utf-8"))
            if idx < len(refreshed):
                tasks = refreshed
                task = tasks[idx]
            impl_fails = 0
            feedback = f"Plan revised ({plan_revisions}/2): {reason}"
            return None

        while True:
            traps = trap_loader(cwd, task["files"], args_dict)
            worker_res = await agent_fn(
                prompt=_worker_prompt(task, cwd, _format_traps_text(traps), feedback),
                schema=WORKER_SCHEMA,
                role=task["title"],
                type_name=task["agent"],
                workspace=workspace_mode,
                model=models.get(task["agent"], DEFAULT_MODELS.get(task["agent"], "pro")),
            )
            w_status = worker_res.get("status", "")

            if w_status == "BLOCKED: spec":
                return {
                    "status": "ESCALATED: spec",
                    "failed_task": task["title"],
                    "reason": worker_res.get("summary", "Blocked by spec"),
                    "tasks": completed,
                }

            if w_status == "BLOCKED: env":
                if not env_flaky_retried:
                    env_flaky_retried = True
                    feedback = f"Environment retry (1/1): {worker_res.get('summary', '')}"
                    continue
                return {
                    "status": "ESCALATED: env",
                    "failed_task": task["title"],
                    "reason": worker_res.get("summary", "Blocked by environment"),
                    "tasks": completed,
                }

            if w_status == "BLOCKED: plan":
                cap_err = await _revise_plan(worker_res.get("summary", "Worker blocked on plan"))
                if cap_err:
                    return {
                        "status": "ESCALATED: plan_cap",
                        "failed_task": task["title"],
                        "reason": cap_err,
                        "tasks": completed,
                    }
                continue

            dispute_note: str | None = None
            if w_status == "DISPUTE":
                if dispute_used:
                    impl_fails += 1
                    reason = f"Second DISPUTE rejected as FAIL: impl ({worker_res.get('summary', '')})"
                    if impl_fails >= 2:
                        cap_err = await _revise_plan(reason)
                        if cap_err:
                            return {
                                "status": "ESCALATED: plan_cap",
                                "failed_task": task["title"],
                                "reason": cap_err,
                                "tasks": completed,
                            }
                    else:
                        feedback = reason
                    continue
                dispute_used = True
                dispute_note = str(worker_res.get("dispute_evidence") or worker_res.get("summary") or "")

            verifier_res = await agent_fn(
                prompt=_verifier_prompt(task, cwd, worker_res, dispute_note=dispute_note),
                schema=VERDICT_SCHEMA,
                role=f"Verify {task['title']}",
                type_name="paddock-verifier",
                workspace=workspace_mode,
                model=models.get("paddock-verifier", DEFAULT_MODELS["paddock-verifier"]),
            )
            verdict = verifier_res.get("verdict")
            v_reason = str(verifier_res.get("reason") or verifier_res.get("summary") or "")

            if verdict == "PASS":
                completed.append(
                    {
                        "task": task["title"],
                        "verdict": "PASS",
                        "summary": verifier_res.get("summary", ""),
                        "evidence": verifier_res.get("evidence", []),
                        "unverified": verifier_res.get("unverified", []),
                        "impl_fails": impl_fails,
                        "plan_revisions": plan_revisions,
                        "dispute_used": dispute_used,
                    }
                )
                break

            if verdict == "FAIL: spec":
                return {
                    "status": "ESCALATED: spec",
                    "failed_task": task["title"],
                    "reason": v_reason,
                    "tasks": completed,
                }

            if verdict == "FAIL: env":
                if not env_flaky_retried:
                    env_flaky_retried = True
                    # Rerun verifier once for flaky test / transient env before escalating
                    rerun_res = await agent_fn(
                        prompt=_verifier_prompt(
                            task,
                            cwd,
                            worker_res,
                            dispute_note="Flaky env rerun (1/1) before escalating FAIL: env.",
                        ),
                        schema=VERDICT_SCHEMA,
                        role=f"Verify rerun {task['title']}",
                        type_name="paddock-verifier",
                        workspace=workspace_mode,
                        model=models.get("paddock-verifier", DEFAULT_MODELS["paddock-verifier"]),
                    )
                    if rerun_res.get("verdict") == "PASS":
                        completed.append(
                            {
                                "task": task["title"],
                                "verdict": "PASS",
                                "summary": rerun_res.get("summary", ""),
                                "evidence": rerun_res.get("evidence", []),
                                "unverified": rerun_res.get("unverified", []),
                                "impl_fails": impl_fails,
                                "plan_revisions": plan_revisions,
                                "dispute_used": dispute_used,
                            }
                        )
                        break
                    v_reason = str(rerun_res.get("reason") or rerun_res.get("summary") or v_reason)
                return {
                    "status": "ESCALATED: env",
                    "failed_task": task["title"],
                    "reason": v_reason,
                    "tasks": completed,
                }

            if verdict == "FAIL: plan":
                cap_err = await _revise_plan(v_reason)
                if cap_err:
                    return {
                        "status": "ESCALATED: plan_cap",
                        "failed_task": task["title"],
                        "reason": cap_err,
                        "tasks": completed,
                    }
                continue

            # FAIL: impl
            impl_fails += 1
            if impl_fails >= 2:
                cap_err = await _revise_plan(f"2x FAIL: impl — {v_reason}")
                if cap_err:
                    return {
                        "status": "ESCALATED: plan_cap",
                        "failed_task": task["title"],
                        "reason": cap_err,
                        "tasks": completed,
                    }
            else:
                feedback = f"FAIL: impl (attempt {impl_fails}/2): {v_reason}"

        idx += 1

    return {"status": "PASS", "plan_path": str(plan_path), "tasks": completed}


async def _run_from_workflow_globals(ns: dict[str, Any]) -> dict[str, Any]:
    wf_args = ns.get("args") if isinstance(ns.get("args"), dict) else {}
    workspaces = ns.get("WORKSPACES") or (".",)
    cwd = str(wf_args.get("worktree") or workspaces[0])
    raw_plan = wf_args.get("plan_path")
    if not raw_plan:
        raise ValueError("build_plan.py requires Args.plan_path")
    p = Path(str(raw_plan))
    plan_path = p if p.is_absolute() else (Path(cwd) / p)
    return await run_build_loop(
        plan_path=plan_path.resolve(),
        cwd=cwd,
        args_dict=wf_args,
        agent_fn=ns["agent"],
        phase_fn=ns.get("phase"),
        log_fn=ns.get("log"),
    )


if "agent" in globals() and callable(globals()["agent"]):
    _workflow_summary = await _run_from_workflow_globals(globals())  # type: ignore[top-level-await]
    print(json.dumps(_workflow_summary, indent=2, ensure_ascii=False))
