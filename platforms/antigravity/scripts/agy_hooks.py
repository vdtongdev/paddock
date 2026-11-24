# /// script
# requires-python = ">=3.12"
# ///
"""Antigravity lifecycle hook handler for Paddock (`SessionStart`, `PreToolUse`, `Stop`)."""

from __future__ import annotations

import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any


def _import_core_module(module_name: str, repo_subdir: Path):
    here = Path(__file__).resolve().parent
    for cand in (here / "paddock_core", here.parents[2] / repo_subdir):
        if (cand / f"{module_name}.py").is_file():
            if str(cand) not in sys.path:
                sys.path.insert(0, str(cand))
            return __import__(module_name)
    raise ImportError(f"Unable to locate Paddock {module_name}.py")


memory = _import_core_module("memory", Path("core") / "memory")
try:
    models = _import_core_module("models", Path("core") / "config")
except ImportError:
    models = None

AGY_ROLES: tuple[str, ...] = ("generalist", "implementer", "ui", "planner", "verifier")
PADDOCK_AGENTS = {f"paddock-{role}" for role in AGY_ROLES}
DEFAULT_AGY_MODELS: dict[str, dict[str, str]] = {
    "generalist": {"swarm_model": "gemini-4-hydrogen-medium", "subagent_alias": "flash"},
    "implementer": {"swarm_model": "gemini-4-neon-medium", "subagent_alias": "pro"},
    "ui": {"swarm_model": "gemini-4-neon-medium", "subagent_alias": "pro"},
    "planner": {"swarm_model": "gemini-4-neon-medium", "subagent_alias": "pro"},
    "verifier": {"swarm_model": "gemini-4-neon-medium", "subagent_alias": "pro"},
}
SWARM_WORKER_RE = re.compile(
    r"^(?:paddock-)?(generalist|implementer|ui|planner|verifier)(?:-[A-Za-z0-9]+)?$",
    re.IGNORECASE,
)
INJECTED_MARKER = "<!-- paddock:injected -->"
VERIFY_RULES = (
    "PADDOCK VERIFICATION CONTRACT:\n"
    "- Report only observed-this-session facts (`command + output`, `file:line`, or `URL + quote`).\n"
    "- End your standalone reply with `## Evidence` and `## Unverified` sections.\n"
    "- Mark new model-checked memory entries `verified: model` (never `human`)."
)
MAX_SESSION_MSG_CHARS = 19500
BRANCH_GUARD_DENY = (
    "Paddock branch-guard: direct edits on 'main'/'master' are blocked outside "
    "docs/specs/, docs/plans/, and ~/.paddock/. Use a feature worktree (.worktrees/<slug>)."
)

SPEC_REF_RE = re.compile(r"docs[/\\]specs[/\\]([A-Za-z0-9._-]+\.md)")
PLAN_REF_RE = re.compile(r"docs[/\\]plans[/\\]([A-Za-z0-9._-]+\.md)")
PATH_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_.:/-])([A-Za-z0-9_.-]+(?:[/\\][A-Za-z0-9_.*-]+)+)")
MODE_RE = re.compile(r"\bMode:\s*(quick|diagnose|git|lookup)\b", re.IGNORECASE)


def default_response(event: str) -> dict[str, Any]:
    if event == "SessionStart":
        return {"injectSteps": []}
    if event == "PreToolUse":
        return {"decision": "allow"}
    if event == "Stop":
        return {"decision": "allow"}
    return {}


def _resolve_cwd(payload: dict[str, Any]) -> Path:
    raw = payload.get("cwd")
    if not raw and isinstance(payload.get("workspacePaths"), list) and payload["workspacePaths"]:
        raw = payload["workspacePaths"][0]
    return Path(str(raw or ".")).expanduser().resolve()


def _is_under(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _is_exempt_doc_path(target: Path, repo_root: Path) -> bool:
    try:
        rel = target.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        rel = target.as_posix().replace("\\", "/")
    return rel.startswith("docs/specs/") or rel.startswith("docs/plans/")


def _git_branch(path_hint: Path, cwd: Path) -> str | None:
    probe = path_hint if path_hint.is_dir() else path_hint.parent
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    if not probe.exists():
        probe = cwd
    try:
        proc = subprocess.run(
            ["git", "-C", str(probe), "branch", "--show-current"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
        if proc.returncode == 0:
            return proc.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def _read_transcript_lines(transcript_path: str | None) -> list[dict[str, Any]]:
    if not transcript_path:
        return []
    p = Path(transcript_path)
    if not p.is_file():
        return []
    records: list[dict[str, Any]] = []
    try:
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                obj = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                records.append(obj)
    except OSError:
        return []
    return records


def _parse_iso_ts(raw: str | None) -> float | None:
    if not raw or not isinstance(raw, str):
        return None
    try:
        clean = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
        dt = datetime.datetime.fromisoformat(clean)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.timestamp()
    except ValueError:
        return None


def _last_user_input_ts(transcript_path: str | None) -> float | None:
    latest: float | None = None
    for obj in _read_transcript_lines(transcript_path):
        if obj.get("type") == "USER_INPUT" and obj.get("source") in ("USER_EXPLICIT", None, ""):
            ts = _parse_iso_ts(obj.get("created_at"))
            if ts is not None:
                latest = ts
    return latest


def _check_approval_gate(file_path: Path, last_user_ts: float | None, gate_name: str) -> str | None:
    if last_user_ts is None:
        return None
    try:
        mtime = file_path.stat().st_mtime
    except OSError:
        return None
    if mtime > last_user_ts + 1.0:
        return (
            f"Paddock {gate_name}: '{file_path.name}' was modified after the latest user message. "
            "Present it to the user and obtain explicit approval before dispatching."
        )
    return None


def _extract_path_candidates(text: str) -> list[str]:
    paths: list[str] = []
    for match in PATH_TOKEN_RE.finditer(text):
        tok = match.group(1).replace("\\", "/").strip("`'\"),.;:")
        if tok and "://" not in tok:
            paths.append(tok)
    return list(dict.fromkeys(paths))


def _resolve_doc_ref(cwd: Path, text: str, kind: str) -> Path | None:
    pattern = SPEC_REF_RE if kind == "specs" else PLAN_REF_RE
    m = pattern.search(text)
    if not m:
        return None
    rel = Path("docs") / kind / m.group(1)
    repo_root = memory.resolve_repo_root(cwd)
    for base in (cwd, repo_root):
        cand = (base / rel).resolve()
        if cand.exists():
            return cand
    return (cwd / rel).resolve()


def _format_traps_block(cwd: Path, paths: list[str]) -> str:
    sel = memory.select(cwd, paths=paths or None, traps_only=True)
    entries = sel.get("entries", [])
    if not entries:
        return ""
    lines = ["MATCHED PADDOCK TRAPS:"]
    for entry in entries[:4]:
        lines.append(f"- [[{entry['name']}]] ({entry['layer']}): {entry['description']}")
        body = entry.get("body", "").strip()
        if body:
            lines.append(body[:600])
    return "\n".join(lines)


def _resolve_antigravity_models(cwd: Path) -> dict[str, dict[str, str]]:
    resolved: dict[str, dict[str, str]] = {r: dict(v) for r, v in DEFAULT_AGY_MODELS.items()}
    if models is None:
        return resolved
    try:
        plat_map = models.resolve_platform("antigravity", cwd)
        if isinstance(plat_map, dict):
            for role in AGY_ROLES:
                entry = plat_map.get(role)
                if not isinstance(entry, dict):
                    continue
                swarm_model = entry.get("swarm_model")
                subagent_alias = entry.get("subagent_alias")
                if isinstance(swarm_model, str) and swarm_model:
                    resolved[role]["swarm_model"] = swarm_model
                if isinstance(subagent_alias, str) and subagent_alias:
                    resolved[role]["subagent_alias"] = subagent_alias
    except Exception as exc:
        print(f"[paddock agy_hooks fail-open] models resolve failed: {exc}", file=sys.stderr)
    return resolved


def _resolve_antigravity_role(cwd: Path, role: str) -> dict[str, str]:
    fallback = dict(
        DEFAULT_AGY_MODELS.get(role, {"swarm_model": "gemini-4-neon-medium", "subagent_alias": "pro"})
    )
    if models is None:
        return fallback
    try:
        entry = models.resolve_role("antigravity", role, cwd)
        if isinstance(entry, dict):
            swarm_model = entry.get("swarm_model")
            subagent_alias = entry.get("subagent_alias")
            if isinstance(swarm_model, str) and swarm_model:
                fallback["swarm_model"] = swarm_model
            if isinstance(subagent_alias, str) and subagent_alias:
                fallback["subagent_alias"] = subagent_alias
    except Exception as exc:
        print(f"[paddock agy_hooks fail-open] role '{role}' resolve failed: {exc}", file=sys.stderr)
    return fallback


def _format_session_models_line(cwd: Path) -> str:
    resolved = _resolve_antigravity_models(cwd)
    parts = [
        f"{role}={resolved[role]['swarm_model']} ({resolved[role]['subagent_alias']})"
        for role in AGY_ROLES
    ]
    return "Paddock Antigravity models (swarm / subagent_alias): " + ", ".join(parts)


def handle_session_start(payload: dict[str, Any]) -> dict[str, Any]:
    cwd = _resolve_cwd(payload)
    idx = memory.index(cwd)
    stale_res = memory.stale(cwd)

    sections: list[str] = []
    for layer in ("project", "user"):
        mdir = memory.layer_dir(cwd, layer)
        mem_file = mdir / "MEMORY.md"
        if mem_file.is_file():
            content = mem_file.read_text(encoding="utf-8", errors="replace").strip()
            if content:
                sections.append(f"### Paddock {layer.capitalize()} Memory ({mdir})\n{content}")

    if stale_res.get("entries"):
        stale_lines = [
            f"- [[{e['name']}]] ({e['layer']}, stale_after={e['stale_after']})"
            for e in stale_res["entries"][:10]
        ]
        sections.append("### Stale Memory Reminders\n" + "\n".join(stale_lines))

    if idx.get("notices"):
        sections.append("### Memory Notices\n" + "\n".join(f"- {n}" for n in idx["notices"]))

    if idx.get("problems"):
        capped = memory.format_problems_capped(idx["problems"], limit=3000)
        if capped:
            sections.append(f"### Memory Validation Problems\n{capped}")

    models_line = _format_session_models_line(cwd)
    body = "\n\n".join(sections)
    if body:
        budget = MAX_SESSION_MSG_CHARS - len(models_line) - 2
        if len(body) > budget:
            trunc_marker = "\n...<truncated>"
            keep = max(0, budget - len(trunc_marker))
            body = body[:keep] + trunc_marker
        full = f"{body}\n\n{models_line}"
    else:
        full = models_line

    if len(full) > MAX_SESSION_MSG_CHARS:
        full = full[: MAX_SESSION_MSG_CHARS - 20] + "\n...<truncated>"
    return {"injectSteps": [{"systemMessage": {"systemMessage": full}}]}


def _handle_branch_guard(cwd: Path, args: dict[str, Any]) -> dict[str, Any]:
    raw_path = args.get("Path") or args.get("TargetFile") or args.get("path") or ""
    if not isinstance(raw_path, str) or not raw_path.strip():
        return {"decision": "allow"}
    p = Path(raw_path)
    target = p.resolve() if p.is_absolute() else (cwd / p).resolve()
    repo_root = memory.resolve_repo_root(cwd)
    if _is_under(target, memory.paddock_home()) or _is_exempt_doc_path(target, repo_root):
        return {"decision": "allow"}
    branch = _git_branch(target, cwd)
    if branch in ("main", "master"):
        return {"decision": "deny", "reason": BRANCH_GUARD_DENY}
    return {"decision": "allow"}


def _validate_git_mode(prompt: str) -> str | None:
    if "--tags" in prompt or "--follow-tags" in prompt:
        return "Paddock Mode: git forbids pushing tags (--tags / --follow-tags)."
    if re.search(r"\bbranch\s+-D\b", prompt) or re.search(r"worktree\s+remove\s+.*--force", prompt):
        return "Paddock Mode: git forbids force-deleting branches/worktrees without user confirmation."
    if re.search(r"\bgit\s+commit\b[^\n]*\s-m\b", prompt) or re.search(r"\bcommit\s+-m\s+['\"]", prompt):
        return "Paddock Mode: git requires intent only; never dictate literal commit messages."
    return None


def _validate_plan_file(plan_file: Path, last_user_ts: float | None) -> str | None:
    if not plan_file.is_file():
        return f"Paddock plan-gate: referenced plan does not exist: {plan_file}"
    text = plan_file.read_text(encoding="utf-8", errors="replace")
    if "Agent:" not in text or "Files:" not in text:
        return f"Paddock plan-gate: plan '{plan_file.name}' must declare 'Agent:' and 'Files:' per task."
    return _check_approval_gate(plan_file, last_user_ts, "Approval Gate 2")


def _handle_run_workflow_gate(cwd: Path, args: dict[str, Any], last_user_ts: float | None) -> dict[str, Any]:
    script_path = str(args.get("ScriptPath") or "")
    wf_args = args.get("Args") if isinstance(args.get("Args"), dict) else {}
    script_text = str(args.get("Script") or "")

    plan_arg = wf_args.get("plan_path")
    is_paddock_build = "build_plan.py" in script_path or isinstance(plan_arg, str)
    if not is_paddock_build:
        return {"decision": "allow"}

    if isinstance(plan_arg, str) and plan_arg.strip():
        p = Path(plan_arg)
        plan_file = p.resolve() if p.is_absolute() else (cwd / p).resolve()
    else:
        plan_file = _resolve_doc_ref(cwd, script_text, "plans")

    if plan_file is None:
        return {
            "decision": "deny",
            "reason": "Paddock plan-gate: build_plan.py requires Args.plan_path naming docs/plans/<file>.md.",
        }

    err = _validate_plan_file(plan_file, last_user_ts)
    if err:
        return {"decision": "deny", "reason": err}
    return {"decision": "allow"}


def _extract_swarm_role(name: str) -> str | None:
    match = SWARM_WORKER_RE.fullmatch(name.strip())
    if not match:
        return None
    return match.group(1).lower()


def _validate_worker_dispatch(
    cwd: Path,
    role: str,
    text: str,
    last_user_ts: float | None,
    *,
    field_label: str,
) -> str | None:
    if role == "planner":
        spec_file = _resolve_doc_ref(cwd, text, "specs")
        plan_file = _resolve_doc_ref(cwd, text, "plans")
        if spec_file is not None:
            if not spec_file.is_file():
                return f"Paddock plan-gate: referenced spec does not exist: {spec_file}"
            return _check_approval_gate(spec_file, last_user_ts, "Approval Gate 1")
        if plan_file is None or not plan_file.is_file():
            return (
                "Paddock plan-gate: paddock-planner requires an approved "
                "docs/specs/<file>.md (or existing plan to revise)."
            )
        return None

    mode_match = MODE_RE.search(text)
    plan_file = _resolve_doc_ref(cwd, text, "plans")
    if not mode_match and plan_file is None:
        return (
            "Paddock plan-gate: specify 'Mode: quick|diagnose|git|lookup' "
            f"or name an approved docs/plans/<file>.md in {field_label}."
        )
    if plan_file is not None and not mode_match:
        err = _validate_plan_file(plan_file, last_user_ts)
        if err:
            return err
    if mode_match and mode_match.group(1).lower() == "git":
        return _validate_git_mode(text)
    return None


def _build_injected_text(cwd: Path, text: str) -> str | None:
    if INJECTED_MARKER in text:
        return None
    paths = _extract_path_candidates(text)
    traps_block = _format_traps_block(cwd, paths)
    injected = f"{INJECTED_MARKER}\n{VERIFY_RULES}"
    if traps_block:
        injected += f"\n\n{traps_block}"
    return f"{text}\n\n{injected}"


def _handle_invoke_subagent_gate(
    cwd: Path, args: dict[str, Any], last_user_ts: float | None
) -> dict[str, Any]:
    type_name = str(args.get("TypeName") or "")
    if type_name not in PADDOCK_AGENTS:
        return {"decision": "allow"}

    role = type_name.removeprefix("paddock-")
    prompt = str(args.get("Prompt") or "")
    err = _validate_worker_dispatch(cwd, role, prompt, last_user_ts, field_label="Prompt")
    if err:
        return {"decision": "deny", "reason": err}

    overwrite: dict[str, Any] = {}
    new_prompt = _build_injected_text(cwd, prompt)
    if new_prompt is not None:
        overwrite["Prompt"] = new_prompt

    raw_model = args.get("Model")
    caller_model = raw_model.strip() if isinstance(raw_model, str) else ""
    if caller_model in ("", "inherit"):
        alias = _resolve_antigravity_role(cwd, role).get("subagent_alias")
        if isinstance(alias, str) and alias and alias != "inherit":
            overwrite["Model"] = alias

    if overwrite:
        return {"decision": "allow", "overwrite": overwrite}
    return {"decision": "allow"}


def _handle_swarm_gate(
    cwd: Path, args: dict[str, Any], last_user_ts: float | None
) -> dict[str, Any]:
    action = str(args.get("action") or "").strip().lower()
    if action != "add":
        return {"decision": "allow"}

    role = _extract_swarm_role(str(args.get("name") or ""))
    if role is None:
        return {"decision": "allow"}

    brief = str(args.get("brief") or "")
    err = _validate_worker_dispatch(cwd, role, brief, last_user_ts, field_label="brief")
    if err:
        return {"decision": "deny", "reason": err}

    overwrite: dict[str, Any] = {}
    new_brief = _build_injected_text(cwd, brief)
    if new_brief is not None:
        overwrite["brief"] = new_brief

    raw_model = args.get("model")
    caller_model = raw_model.strip() if isinstance(raw_model, str) else ""
    if not caller_model:
        swarm_model = _resolve_antigravity_role(cwd, role).get("swarm_model")
        if isinstance(swarm_model, str) and swarm_model:
            overwrite["model"] = swarm_model

    if overwrite:
        return {"decision": "allow", "overwrite": overwrite}
    return {"decision": "allow"}


def handle_pre_tool_use(payload: dict[str, Any]) -> dict[str, Any]:
    if os.environ.get("PADDOCK_SOLO") == "1":
        return {"decision": "allow"}

    cwd = _resolve_cwd(payload)
    tool_call = payload.get("toolCall") if isinstance(payload.get("toolCall"), dict) else {}
    tool_name = str(tool_call.get("name") or "")
    args = tool_call.get("args") if isinstance(tool_call.get("args"), dict) else {}

    if tool_name in ("edit_file", "write_file"):
        return _handle_branch_guard(cwd, args)

    last_user_ts = _last_user_input_ts(payload.get("transcriptPath"))
    if tool_name == "run_workflow":
        return _handle_run_workflow_gate(cwd, args, last_user_ts)
    if tool_name == "invoke_subagent":
        return _handle_invoke_subagent_gate(cwd, args, last_user_ts)
    if tool_name == "swarm":
        return _handle_swarm_gate(cwd, args, last_user_ts)

    return {"decision": "allow"}


def handle_stop(payload: dict[str, Any]) -> dict[str, Any]:
    if os.environ.get("PADDOCK_SOLO") == "1":
        return {"decision": "allow"}
    if payload.get("continuationActive"):
        return {"decision": "allow"}
    if payload.get("terminationReason") != "NO_TOOL_CALL":
        return {"decision": "allow"}

    records = _read_transcript_lines(payload.get("transcriptPath"))
    if not records:
        return {"decision": "allow"}

    first = records[0]
    if first.get("type") == "USER_INPUT":
        return {"decision": "allow"}
    if first.get("type") != "SYSTEM_MESSAGE" or INJECTED_MARKER not in str(first.get("content") or ""):
        return {"decision": "allow"}

    last_reply = ""
    for rec in reversed(records):
        if rec.get("type") == "PLANNER_RESPONSE" and isinstance(rec.get("content"), str):
            last_reply = rec["content"]
            break

    if "## Evidence" not in last_reply or "## Unverified" not in last_reply:
        return {
            "decision": "continue",
            "reason": (
                "Paddock evidence-check: standalone Paddock subagents must end with "
                "`## Evidence` and `## Unverified` sections reporting observed-this-session proof."
            ),
        }
    return {"decision": "allow"}


def process_event(event: str, payload: dict[str, Any]) -> dict[str, Any]:
    if event == "SessionStart":
        return handle_session_start(payload)
    if event == "PreToolUse":
        return handle_pre_tool_use(payload)
    if event == "Stop":
        return handle_stop(payload)
    return {}


def main(argv: list[str] | None = None) -> int:
    cli_args = argv if argv is not None else sys.argv[1:]
    fallback_event = cli_args[0] if cli_args else "unknown"
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            payload = {}
        event = str(payload.get("hookEventName") or fallback_event)
        response = process_event(event, payload)
    except Exception as exc:
        print(f"[paddock agy_hooks fail-open] {exc}", file=sys.stderr)
        response = default_response(fallback_event)

    sys.stdout.buffer.write((json.dumps(response, ensure_ascii=False) + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
