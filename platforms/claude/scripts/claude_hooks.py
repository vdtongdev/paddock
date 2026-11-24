# /// script
# requires-python = ">=3.12"
# ///
"""Claude Code lifecycle hook handler for Paddock."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any


def _import_memory():
    here = Path(__file__).resolve().parent
    for cand in (here / "paddock_core", here.parents[2] / "core" / "memory"):
        if (cand / "memory.py").is_file():
            if str(cand) not in sys.path:
                sys.path.insert(0, str(cand))
            import memory  # type: ignore[import-not-found]

            return memory
    raise ImportError("Unable to locate Paddock memory.py")


memory = _import_memory()

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
SEARCH_TOOLS = {"Grep", "Glob", "WebFetch", "WebSearch"}
PADDOCK_ROLES = {"generalist", "implementer", "ui", "planner", "builder", "verifier"}

SPEC_REF_RE = re.compile(r"docs[/\\]specs[/\\]([A-Za-z0-9._-]+\.md)")
PLAN_REF_RE = re.compile(r"docs[/\\]plans[/\\]([A-Za-z0-9._-]+\.md)")
MODE_RE = re.compile(r"\bMode:\s*(quick|diagnose|git|lookup)\b", re.IGNORECASE)
VERDICT_LINE_RE = re.compile(r"(?m)^\s*(PASS|FAIL:\s*(?:impl|plan|spec|env))\s*$")

ORCHESTRATOR_RULES = (
    "PADDOCK ORCHESTRATOR RULES:\n"
    "- Keep main session pure: clarify, write specs, review plans, judge escalations.\n"
    "- Bugs always start with implementer (`Mode: diagnose`) before fixing.\n"
    "- Stop for User Approval Gate 1 after writing spec and Gate 2 after reviewing plan.\n"
    "- Route git writes through implementer (`Mode: git`) with intent only."
)
VERIFY_RULES = (
    "PADDOCK VERIFY RULES:\n"
    "- Cite observed-this-session evidence only (`command + output`, `file:line`, `URL + quote`).\n"
    "- End with `## Evidence` and `## Unverified` sections.\n"
    "- Verifier must emit exactly one verdict line: PASS | FAIL: impl | FAIL: plan | FAIL: spec | FAIL: env."
)


def _state_dir() -> Path:
    plugin_data = os.environ.get("CLAUDE_PLUGIN_DATA", "").strip()
    if plugin_data:
        return Path(plugin_data).expanduser().resolve()
    return memory.paddock_home() / "claude-state"


def _read_json_state(filename: str) -> dict[str, Any]:
    path = _state_dir() / filename
    if not path.is_file():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        return loaded if isinstance(loaded, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_json_state(filename: str, data: dict[str, Any]) -> None:
    path = _state_dir() / filename
    memory.atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def _deny(reason: str) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def _allow_with_context(context: str) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "additionalContext": context,
        }
    }


def _is_under(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _is_exempt_doc(path: Path, repo_root: Path) -> bool:
    try:
        rel = path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        rel = path.as_posix().replace("\\", "/")
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


def _resolve_doc_ref(cwd: Path, text: str, kind: str) -> Path | None:
    pat = SPEC_REF_RE if kind == "specs" else PLAN_REF_RE
    m = pat.search(text)
    if not m:
        return None
    rel = Path("docs") / kind / m.group(1)
    repo_root = memory.resolve_repo_root(cwd)
    for base in (cwd, repo_root):
        cand = (base / rel).resolve()
        if cand.exists():
            return cand
    return (cwd / rel).resolve()


def _last_prompt_ts(session_id: str | None) -> float | None:
    prompts = _read_json_state("prompts.json")
    entry = prompts.get(session_id) if session_id and session_id in prompts else prompts.get("_latest")
    if isinstance(entry, dict) and isinstance(entry.get("ts"), (int, float)):
        return float(entry["ts"])
    return None


def _check_approval_gate(file_path: Path, prompt_ts: float | None, gate_label: str) -> str | None:
    if prompt_ts is None:
        return None
    try:
        mtime = file_path.stat().st_mtime
    except OSError:
        return None
    if mtime > prompt_ts + 1.0:
        return (
            f"Paddock {gate_label}: '{file_path.name}' was edited after the latest user prompt. "
            "Stop and request explicit user approval first."
        )
    return None


def _validate_git_mode(prompt: str) -> str | None:
    if "--tags" in prompt or "--follow-tags" in prompt:
        return "Paddock Mode: git forbids pushing tags (--tags / --follow-tags)."
    if re.search(r"\bbranch\s+-D\b", prompt) or re.search(r"worktree\s+remove\s+.*--force", prompt):
        return "Paddock Mode: git forbids force-deleting branches/worktrees without user confirmation."
    if re.search(r"\bgit\s+commit\b[^\n]*\s-m\b", prompt) or re.search(r"\bcommit\s+-m\s+['\"]", prompt):
        return "Paddock Mode: git requires intent only; never dictate literal commit messages."
    return None


def _normalize_role(subagent_type: str) -> str:
    return subagent_type.split(":")[-1].strip().lower()


def handle_pre_tool_use(payload: dict[str, Any]) -> dict[str, Any]:
    if os.environ.get("PADDOCK_SOLO") == "1":
        return {}

    cwd = Path(str(payload.get("cwd") or ".")).expanduser().resolve()
    tool_name = str(payload.get("tool_name") or "")
    tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    is_subagent = bool(payload.get("agent_id"))

    if tool_name in SEARCH_TOOLS:
        if not is_subagent:
            return _deny(
                f"Paddock search-guard: main session cannot invoke {tool_name}; "
                "delegate to `generalist` with `Mode: lookup`."
            )
        return {}

    if tool_name in EDIT_TOOLS:
        raw_path = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
        if not raw_path.strip():
            return {}
        p = Path(raw_path)
        target = p.resolve() if p.is_absolute() else (cwd / p).resolve()
        repo_root = memory.resolve_repo_root(cwd)
        exempt = _is_under(target, memory.paddock_home()) or _is_exempt_doc(target, repo_root)

        if not is_subagent and not exempt:
            return _deny(
                "Paddock guard: main session cannot edit code directly outside specs/plans/memory; "
                "dispatch a worker or builder."
            )

        if is_subagent and not exempt:
            branch = _git_branch(target, cwd)
            if branch in ("main", "master"):
                return _deny(
                    "Paddock branch-guard: subagents cannot edit code directly on 'main'/'master'; "
                    "work inside a feature branch / .worktrees/<slug>."
                )

        if is_subagent:
            sel = memory.select(cwd, paths=[str(target)], traps_only=True)
            entries = sel.get("entries", [])
            if entries:
                lines = ["Matched Paddock traps for edited file:"]
                for e in entries[:4]:
                    lines.append(f"- [[{e['name']}]]: {e['description']}\n{e.get('body', '').strip()[:500]}")
                return _allow_with_context("\n".join(lines))
        return {}

    if tool_name == "Agent":
        subagent_type = str(tool_input.get("subagent_type") or "")
        role = _normalize_role(subagent_type)
        if role not in PADDOCK_ROLES:
            return {}

        prompt = str(tool_input.get("prompt") or "")
        prompt_ts = _last_prompt_ts(payload.get("session_id"))

        if role == "planner":
            spec_file = _resolve_doc_ref(cwd, prompt, "specs")
            plan_file = _resolve_doc_ref(cwd, prompt, "plans")
            if spec_file is not None:
                if not spec_file.is_file():
                    return _deny(f"Paddock plan-gate: spec not found: {spec_file}")
                err = _check_approval_gate(spec_file, prompt_ts, "Approval Gate 1")
                if err:
                    return _deny(err)
            elif plan_file is None or not plan_file.is_file():
                return _deny("Paddock plan-gate: planner requires an approved docs/specs/<file>.md (or existing plan).")
            return {}

        if role == "builder":
            plan_file = _resolve_doc_ref(cwd, prompt, "plans")
            if plan_file is None or not plan_file.is_file():
                return _deny("Paddock plan-gate: builder requires a named existing docs/plans/<file>.md.")
            text = plan_file.read_text(encoding="utf-8", errors="replace")
            if "Agent:" not in text or "Files:" not in text:
                return _deny(f"Paddock plan-gate: plan '{plan_file.name}' must include 'Agent:' and 'Files:'.")
            err = _check_approval_gate(plan_file, prompt_ts, "Approval Gate 2")
            if err:
                return _deny(err)
            return {}

        mode_match = MODE_RE.search(prompt)
        plan_file = _resolve_doc_ref(cwd, prompt, "plans")
        if not mode_match and plan_file is None:
            return _deny(
                "Paddock plan-gate: specify 'Mode: quick|diagnose|git|lookup' "
                "or reference an approved docs/plans/<file>.md."
            )
        if plan_file is not None and not mode_match:
            if not plan_file.is_file():
                return _deny(f"Paddock plan-gate: plan not found: {plan_file}")
            text = plan_file.read_text(encoding="utf-8", errors="replace")
            if "Agent:" not in text or "Files:" not in text:
                return _deny(f"Paddock plan-gate: plan '{plan_file.name}' must include 'Agent:' and 'Files:'.")
            err = _check_approval_gate(plan_file, prompt_ts, "Approval Gate 2")
            if err:
                return _deny(err)
        if mode_match and mode_match.group(1).lower() == "git":
            git_err = _validate_git_mode(prompt)
            if git_err:
                return _deny(git_err)
        return {}

    return {}


def handle_post_tool_use(payload: dict[str, Any]) -> dict[str, Any]:
    cwd = Path(str(payload.get("cwd") or ".")).expanduser().resolve()
    tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    raw_path = str(tool_input.get("file_path") or "")
    if not raw_path:
        return {}
    norm = raw_path.replace("\\", "/")
    if "docs/plans/" in norm and norm.endswith(".md"):
        plans = _read_json_state("plans.json")
        sid = str(payload.get("session_id") or "_latest")
        recorded = plans.get(sid) if isinstance(plans.get(sid), list) else []
        resolved = str((Path(raw_path) if Path(raw_path).is_absolute() else cwd / raw_path).resolve())
        if resolved not in recorded:
            recorded.append(resolved)
        plans[sid] = recorded
        _write_json_state("plans.json", plans)
    return {}


def handle_user_prompt_submit(payload: dict[str, Any]) -> dict[str, Any]:
    prompts = _read_json_state("prompts.json")
    now = time.time()
    sid = payload.get("session_id")
    rec = {"ts": now}
    if isinstance(sid, str) and sid:
        prompts[sid] = rec
    prompts["_latest"] = rec
    _write_json_state("prompts.json", prompts)
    return {}


def handle_session_start(payload: dict[str, Any]) -> dict[str, Any]:
    cwd = Path(str(payload.get("cwd") or ".")).expanduser().resolve()
    idx = memory.index(cwd)
    stale_res = memory.stale(cwd)

    sections = [ORCHESTRATOR_RULES]
    for layer in ("project", "user"):
        mfile = memory.layer_dir(cwd, layer) / "MEMORY.md"
        if mfile.is_file():
            content = mfile.read_text(encoding="utf-8", errors="replace").strip()
            if content:
                sections.append(f"### Paddock {layer.capitalize()} Memory\n{content}")

    if stale_res.get("entries"):
        sections.append(
            "### Stale Memory Reminders\n"
            + "\n".join(f"- [[{e['name']}]] (stale_after={e['stale_after']})" for e in stale_res["entries"][:10])
        )
    if idx.get("notices"):
        sections.append("### Memory Notices\n" + "\n".join(f"- {n}" for n in idx["notices"]))
    if idx.get("problems"):
        capped = memory.format_problems_capped(idx["problems"], limit=3000)
        if capped:
            sections.append(f"### Memory Validation Problems\n{capped}")

    return {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": "\n\n".join(sections),
        }
    }


def handle_subagent_start(payload: dict[str, Any]) -> dict[str, Any]:
    cwd = Path(str(payload.get("cwd") or ".")).expanduser().resolve()
    traps = memory.select(cwd, traps_only=True).get("entries", [])
    parts = [VERIFY_RULES]
    if traps:
        parts.append(
            "Active Paddock Traps:\n"
            + "\n".join(f"- [[{t['name']}]] ({t['layer']}): {t['description']}" for t in traps[:8])
        )
    return {
        "hookSpecificOutput": {
            "hookEventName": "SubagentStart",
            "additionalContext": "\n\n".join(parts),
        }
    }


def _extract_last_assistant_message(payload: dict[str, Any]) -> str:
    direct = payload.get("last_assistant_message")
    if isinstance(direct, str) and direct.strip():
        return direct

    tpath = payload.get("agent_transcript_path") or payload.get("transcript_path")
    if not tpath or not Path(str(tpath)).is_file():
        return ""
    last_text = ""
    for line in Path(str(tpath)).read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            msg = obj.get("message") if isinstance(obj.get("message"), dict) else obj
            if msg.get("role") == "assistant" or obj.get("type") == "assistant":
                content = msg.get("content")
                if isinstance(content, str):
                    last_text = content
                elif isinstance(content, list):
                    texts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
                    if texts:
                        last_text = "\n".join(texts)
    return last_text


def handle_subagent_stop(payload: dict[str, Any]) -> dict[str, Any]:
    if os.environ.get("PADDOCK_SOLO") == "1":
        return {}
    if payload.get("stop_hook_active"):
        return {}

    text = _extract_last_assistant_message(payload)
    if not text.strip():
        return {}

    missing = [sec for sec in ("## Evidence", "## Unverified") if sec not in text]
    if missing:
        return {
            "decision": "block",
            "reason": f"Paddock evidence-check: missing required section(s): {', '.join(missing)}.",
        }

    agent_type = _normalize_role(str(payload.get("agent_type") or ""))
    if agent_type == "verifier":
        verdicts = VERDICT_LINE_RE.findall(text)
        if len(verdicts) != 1:
            return {
                "decision": "block",
                "reason": (
                    f"Paddock evidence-check: verifier must emit exactly one verdict line "
                    f"(found {len(verdicts)})."
                ),
            }
    return {}


EVENT_HANDLERS = {
    "pre-tool-use": handle_pre_tool_use,
    "PreToolUse": handle_pre_tool_use,
    "post-tool-use": handle_post_tool_use,
    "PostToolUse": handle_post_tool_use,
    "user-prompt-submit": handle_user_prompt_submit,
    "UserPromptSubmit": handle_user_prompt_submit,
    "session-start": handle_session_start,
    "SessionStart": handle_session_start,
    "subagent-start": handle_subagent_start,
    "SubagentStart": handle_subagent_start,
    "subagent-stop": handle_subagent_stop,
    "SubagentStop": handle_subagent_stop,
}


def main(argv: list[str] | None = None) -> int:
    cli_args = argv if argv is not None else sys.argv[1:]
    event = cli_args[0] if cli_args else ""
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            payload = {}
        ev = str(event or payload.get("hook_event_name") or "")
        handler = EVENT_HANDLERS.get(ev)
        response = handler(payload) if handler else {}
    except Exception as exc:
        print(f"[paddock claude_hooks fail-open] {exc}", file=sys.stderr)
        response = {}

    sys.stdout.buffer.write((json.dumps(response, ensure_ascii=False) + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
