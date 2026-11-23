# Phase 1 Pre-Implementation Spike Results (Spikes 1–3)

Date: 2026-11-23
Environment: Windows (`Python 3.14.5`, `uv 0.13.7`, `pytest 9.2.0`, `git`, `gh 2.89.0`, `codegraph 1.6.4`, `claude 2.1.329`)
Repo state: `D:/Projects/paddock` on `main` (`70eb05c`)

---

## Summary

| Spike | Outcome | Key Finding |
| :--- | :--- | :--- |
| **1(a) Hook payloads & identity** | **Verified (adjusted)** | `run_workflow` agents share `USER_INPUT` / `lastUserInput` structure with root; only `invoke_subagent` children have `lastUserInput: ""` and `SYSTEM_MESSAGE`. Keep `guard`/`search-guard` in `rules/AGENTS.md` + Plan Mode rather than mechanical `PreToolUse` on `edit_file`. |
| **1(b) `PreToolUse` `overwrite`** | **PASS** | `{"decision": "allow", "overwrite": {"Prompt": ...}}` rewrites `invoke_subagent.Prompt` cleanly; replay notice is `"A pre-tool hook changed the arguments of this tool call before it ran. Changed: Prompt."`; child saw injected text and echoed `TRAP_ACK_7742`. |
| **1(c) Approval-gate signal** | **Verified (simplified)** | Reading `transcriptPath` directly on `PreToolUse` (`invoke_subagent\|run_workflow`) gives `USER_INPUT` (`source: "USER_EXPLICIT"`, `step_index`, `created_at`) with zero state file and no per-invocation hook overhead. |
| **1(d) `cmd /c` hook latency** | **PASS (< 300 ms warm)** | Host warm median: `python` `89.31 ms` (`95.71 ms` cold); `uv run --no-project --script` `137.19 ms` (`600.11 ms` cold, `2439.20 ms` with empty cache). |
| **2 `run_workflow` & `workspace="share"`** | **PASS (with lifecycle nuances)** | `run_workflow` executes on Windows without `python3` alias and resolves plugin `paddock-verifier` (`mainAgent: false`, `tools:` allowlist) with `finish` schema (`70.532s` total for 2 agents). Post-restart, `workspace="share"` works cleanly (`50.23s`), copies untracked files, isolates `.codegraph`, shares `git_common_dir`, and persists after workflow exit (creating `workflow-*` branches outside the workspace root under `D:/antigravity-worktrees/`). Keep explicit `.worktrees/<slug>` for PR delivery branches. |
| **3 Stdlib stdio MCP (`paddock-mem`)** | **PASS** | Passed direct Windows stdio harness (`python` + `uv`, LF-only), live Antigravity `call_mcp_tool` (`paddock-spike_paddock-mem-spike`), and Claude Code CLI (`claude -p` with `uv`, `negotiatedProtocolVersion: "2025-11-25"`). |

---

## Spike 1 — Antigravity Hook Payload, Identity, Overwrite & Latency

### 1(a) Root vs. `invoke_subagent` vs. `run_workflow` Payloads

Observed across root (`4dab8211`), `invoke_subagent` (`20c68274`, `7e5c643f`, `2433bf78`), and `run_workflow` (`4e705811`, `5237f0f3`):

| Field / Signal | Root (`4dab8211`) | `invoke_subagent` (`research` / `self`) | `run_workflow` (`self` / `paddock-verifier`) |
| :--- | :--- | :--- | :--- |
| Events observed | `PreInvocation`, `PreToolUse` | `SessionStart`, `PreInvocation`, `PreToolUse`, `PostToolUse`, `Stop` | `SessionStart`, `PreInvocation`, `PreToolUse`, `Stop` |
| `lastUserInput` | User prompt string | `""` (empty string) | Full agent prompt ending with `"\n\nWhen you are done, call the finish tool with your result."` |
| `transcript_full.jsonl` line 0 `type` / `source` | `USER_INPUT` / `USER_EXPLICIT` | `SYSTEM_MESSAGE` / `SYSTEM` | `USER_INPUT` / `USER_EXPLICIT` |
| `last_user_input` in transcript | Latest user turn (`step_index: 41`, `created_at`) | `null` | Step `0` (`created_at`) |
| `modelName` | `"gemini-4-neon-xhigh"` | `"gemini-3.5-flash-lite"` | `"gemini-3.5-flash-lite"` |
| `Stop.terminationReason` | Not triggered during run | `"NO_TOOL_CALL"` | `"TERMINAL_STEP_TYPE"` |
| Hook process env | `ANTIGRAVITY_CONVERSATION_ID`, `PLUGIN_ROOT`, `UV_*` | Same | Same |

Additional operational findings:
- **Plugin hook lifecycle & fail-closed behavior:** Creating `.agents/plugins/paddock-spike/hooks.json` mid-turn did not activate hooks inside the already-running root turn (`4dab8211`), activated immediately for newly spawned subagents (`20c68274`, `7e5c643f`, `2433bf78`), and fired on root `4dab8211` on subsequent turns. Loaded hooks stay cached in memory for the remainder of the server session even if deleted on disk, and non-zero hook exits (`exit status 2`) fail closed and block tool calls — every hook must catch all exceptions and exit `0`.
- **Windows stdin encoding pitfall:** Antigravity does **not** set `PYTHONUTF8=1` in hook subprocess environments. Reading `sys.stdin.read()` under default Windows encoding mojibaked UTF-8 en-dash (`–`) in `lastUserInput` as `â€“`. Production hooks must read binary stdin via `sys.stdin.buffer.read().decode("utf-8")`.
- **Concurrent hook writes:** Two concurrent hook invocations appending to an unlocked JSONL file tore one record (`BAD_LINE 72`). Production hook state/logging must write atomically or log only to stderr.

**Decision for `guard` and `search-guard`:**
- Do **not** infer "main session vs. worker" from `lastUserInput != ""` or `first_line.type == "USER_INPUT"` — `run_workflow` agents (`paddock-implementer`, `paddock-ui`) have identical `USER_INPUT` / `lastUserInput` fields and would be falsely blocked from editing.
- Keep `guard` (main-session no-edit) and `search-guard` (main-session no-`search_web`) enforced by `rules/AGENTS.md` + Antigravity Plan Mode on Antigravity (the fallback anticipated in Spec Section 4.1 #9).
- Keep mechanical `PreToolUse` enforcement in `agy_hooks.py` for `branch-guard` (`edit_file|write_file` off `main`/`master` outside specs/plans/memory) and `plan-gate` (`invoke_subagent|run_workflow` mode, plan structure, and approval-gate mtime).

### 1(b) `PreToolUse` `overwrite` on `invoke_subagent.Prompt`

Tested inside `7e5c643f` calling `invoke_subagent(TypeName="research", Prompt="SPIKE1B_TEST_OVERWRITE: ...")`:
- Hook stdout: `{"decision": "allow", "overwrite": {"Prompt": "<original>\n\n[SPIKE_INJECTED_TRAP]: Include the exact token TRAP_ACK_7742 in your reply."}}`
- Tool result notice returned to caller: `"A pre-tool hook changed the arguments of this tool call before it ran. Changed: Prompt."`
- `PostToolUse` received the rewritten `toolCall.args.Prompt`.
- Child subagent `2433bf78` received the appended trap instruction and returned verbatim: `TRAP_ACK_7742`.

**Decision:** Keep `PreToolUse` `overwrite` on `invoke_subagent.Prompt` for standalone quick-lane/diagnose trap + `VERIFY_RULES` injection, and inject directly in Python inside `workflows/build_plan.py` for plan-lane workflow tasks.

### 1(c) `PreInvocation` & Approval-Gate Signal

- `PreInvocation` payload keys: `artifactDirectoryPath`, `conversationId`, `cwd`, `hookEventName`, `initialNumSteps`, `invocationNum`, `lastUserInput`, `modelName`, `transcriptPath`, `workspacePaths`.
- `transcriptPath` points to `transcript_full.jsonl`, whose `USER_INPUT` lines contain `step_index`, `source: "USER_EXPLICIT"`, `created_at` (UTC ISO-8601), and `content`.

**Decision:** Instead of spawning a `PreInvocation` hook before every single model invocation and tracking `lastUserInput` text diffs in a sidecar file (which breaks if the user sends `"ok"` twice), inspect `transcriptPath` directly inside `PreToolUse` on `invoke_subagent|run_workflow`: compare the latest `USER_INPUT` entry's `created_at` (and `step_index`) against the spec/plan file's mtime. Zero state files and zero per-invocation latency.

### 1(d) `cmd /c` Hook Latency Benchmark (5 Runs Each)

| Runner | Mode | Cold (ms) | Warm Runs (ms) | Warm Min (ms) | Warm Median (ms) | Warm Max (ms) | Target (< 300 ms) |
| :--- | :--- | :---: | :--- | :---: | :---: | :---: | :--- |
| `python` | Sandboxed | 136.63 | 117.80, 128.26, 132.16, 130.84 | 117.80 | **129.55** | 132.16 | PASS (cold & warm) |
| `uv run --no-project --script` | Sandboxed (`scratch/uv-cache`) | 2439.20 | 728.96, 202.45, 190.54, 179.38 | 179.38 | **196.50** | 728.96 | PASS (warm runs 3–5) |
| `python` | Host (`BypassSandbox`) | 95.71 | 90.03, 87.78, 88.58, 101.08 | 87.78 | **89.31** | 101.08 | PASS (cold & warm) |
| `uv run --no-project --script` | Host (`D:\dev-cache\uv`) | 600.11 | 278.95, 131.52, 130.88, 142.87 | 130.88 | **137.19** | 278.95 | PASS (warm) |

Note: Inside Antigravity's terminal sandbox, `UV_CACHE_DIR=D:\dev-cache\uv` fails with `Access is denied. (os error 5)`, whereas Antigravity's own hook runner executes with host cache access (`UV_CACHE_DIR=D:\dev-cache\uv` observed in hook env). `python` is consistently faster (`~89 ms` warm, `~96 ms` cold), while `uv run --no-project --script` passes the `< 300 ms` warm target (`137.19 ms` median) once cached.

---

## Spike 2 — `run_workflow` & `workspace="share"`

### 1. Windows Workflow & Plugin Agent Resolution

- **`run_workflow` on Windows (`PASS`):** Executes Python scripts directly without requiring a `python3` alias on Windows `PATH`.
- **Plugin custom agent resolution (`PASS`):** `await agent(type_name="paddock-verifier", model="flash_lite", workspace="inherit", schema=VERDICT_SCHEMA, ...)` resolved `.agents/plugins/paddock-spike/agents/paddock-verifier.md` directly.
  - `subagent: true` + `mainAgent: false` worked without needing `mainAgent: true`.
  - `tools: [read_file, run_command, manage_task]` did **not** block `finish` schema submission; `paddock-verifier` returned structured JSON (`verdict: "PASS"`).
- **Workflow timing:** Sequential `self` (`step1_sec = 29.358s`) + `paddock-verifier` (`step2_sec = 41.158s`) totaled `70.532s` (`flash_lite`), eliminating the `builder` LLM handoff layer cleanly.

### 2. `workspace="share"` Behavior (Pre-Restart vs. Post-Restart)

- **Pre-restart (when `git init -b main` ran mid-session):** Both `run_workflow` and `invoke_subagent` failed on `workspace="share"` (`share workspace not supported for VCS type Unknown`) and `workspace="branch"` (`refusing to branch D:\Projects\paddock by copying: it is in a Git repository at file:///d:/Projects/paddock that the language server did not detect when the workspace was opened; reopen the workspace to branch it with its VCS`).
- **Post-restart (after `.git` detected on open, `task-180`, `50.23s`):** `await agent(..., workspace="share")` **succeeded**:
  - **Worktree path & repo slug:** Created real git worktree `D:/antigravity-worktrees/4dab8211-6125-487d-a8cb-5dc2c4603d1e/workflow-self-02db7521` (also surfaced under `C:\Users\tong2\.gemini\antigravity\worktrees\...`), `git_dir` = `D:/Projects/paddock/.git/worktrees/workflow-self-02db7521`, `git_common_dir` = `D:/Projects/paddock/.git` (matching main checkout for unified memory slug).
  - **Untracked files:** `spike2_untracked_probe.txt` was present inside the shared worktree (`untracked_probe_exists: true`, `status_short: "?? spike2_untracked_probe.txt"`), so untracked specs/plans are automatically visible.
  - **Branching & commits:** Antigravity auto-created branch `workflow-self-02db7521`; the agent checked out `spike2-branch-test` and committed `9ee889c` cleanly.
  - **`codegraph` isolation:** `codegraph init -y` exited `0` and created `.codegraph/` inside the shared worktree without leaking into `D:/Projects/paddock` (`main_codegraph_exists: false`).
  - **Persistence & cleanup:** Both the worktree directory (`SHARE_DIR_EXISTS=True`) and branches (`spike2-branch-test`, `workflow-self-02db7521`) **persisted** after agent exit AND after `run_workflow` exit. Because the worktree lives outside the workspace (`D:/antigravity-worktrees/`), `git worktree remove` inside the terminal sandbox failed (`Invalid argument` / ref lock permission denied) and required unsandboxed cleanup (`BypassSandbox: true`).
- **Host tooling:** `codegraph 1.6.4` and `gh 2.89.0` (logged in as `vdtongdev` via `keyring`; requires unsandboxed execution) confirmed operational.

**Decisions:**
- **Drop `paddock-builder.md` on Antigravity (Spec Section 8 #2 confirmed):** Implement deterministic `workflows/build_plan.py` (`run_workflow`) using `paddock-*` custom agents and JSON-schema `finish` contracts.
- **Keep explicit `.worktrees/<slug>` for PR delivery branches:** Predictable workspace-local paths (`.worktrees/<slug>` via `.git/info/exclude`), clean branch naming without orphan `workflow-*` branches, works even on newly `git init`ed repos before reload, and stays removable without touching external `D:/antigravity-worktrees/`. Use `workspace="share"` optionally for parallel/isolated `run_workflow` sub-tasks that do not manage persistent PR branches.

---

## Spike 3 — Stdlib `paddock-mem` MCP Server

### 1. Direct Windows Stdio Harness (`test_spike_mcp.py`, `scratch_uv`)

- **`python` (`148.81 ms`):** Passed `initialize` (`2025-03-26`), `notifications/initialized` (no stray output), `ping`, `tools/list` (`["mem_ping", "mem_select"]`), `tools/call` (`mem_ping`, `mem_select`), `crlf_clean: true` (strict LF `b"\n"`, zero `b"\r"` bytes), and exit `0` on stdin EOF.
- **`uv run --no-project --script` (`1721.29 ms` cold into `scratch/uv-cache`):** Passed identical assertions (`python: 3.14.10`, `crlf_clean: true`, exit `0`).

### 2. Antigravity Workspace Plugin (`mcp_config.json`)

- Antigravity discovered `.agents/plugins/paddock-spike/mcp_config.json` and spawned `spike_mcp.py` (`pid=24720`, `python=3.14.5`, `cwd=d:\Projects\paddock\.agents\plugins\paddock-spike`).
- Registered server name is **namespaced as `<plugin_dir>_<server_name>`**: `paddock-spike_paddock-mem-spike` (schema files at `C:\Users\tong2\.gemini\antigravity\mcp\paddock-spike_paddock-mem-spike\`).
- Handshake sent: `server/discover` (`id=1`) → `initialize` (`id=2`) → `notifications/initialized` (`id=None`) → `tools/list` (`id=3`).
- Live `call_mcp_tool` calls to `mem_ping(echo="agy-ok")` and `mem_select(cwd="D:/Projects/paddock", paths=["docs/specs/paddock-spec.md"], topics=["mcp"])` both returned `"ok": true`.
- Note: `GetAllPlugins` RPC returned empty `mcp` for `paddock-spike` even while its MCP server was active and callable.

### 3. Claude Code CLI on Windows (`claude 2.1.329`)

- **Unsandboxed host run (`claude_mcp_uv.json`, `BypassSandbox: true`):**
  - `uv` spawned `spike_mcp.py` (`pid=19760`, `python=3.14.10`, `cwd=D:\Projects\paddock`) in `930 ms`.
  - Sent `server/discover` (`id='server-discover-probe-1'`) → `initialize` (`id=0`, negotiated `"2025-11-25"`) → `notifications/initialized` → `tools/list` (`id=1`) → `tools/call` `mem_ping` (`id=2`, `10 ms`) → clean stdin EOF shutdown (`4s`).
  - Exited cleanly with `is_error: false`, `subtype: "success"`, `num_turns: 3`, result `{"ok": true, "echo": "claude-ok", "pid": 19760, "python": "3.14.10", "cwd": "D:\\Projects\\paddock"}`.
- **Sandboxed run:** Failed as expected (`Not logged in · Please run /login` without `~/.claude/`, and `uv` failed on `D:\dev-cache\uv\.lock: Access is denied. (os error 5)`).

**MCP Server Protocol Requirements Confirmed:**
- Write strict UTF-8 LF (`b"\n"`) via `sys.stdout.buffer` (never text mode `sys.stdout` on Windows).
- Return JSON-RPC `-32601` cleanly for `server/discover` probes (sent by both Antigravity and Claude Code before `initialize`).
- Echo supported `protocolVersion` including `"2025-11-25"` and `"2025-03-26"`.
- Derive git repo root strictly from each tool call's explicit `cwd` parameter (`os.getcwd()` inside Antigravity plugin MCP processes is `PLUGIN_ROOT`, not the user's repo).

---

## Phase 1 Architecture Adjustments (Locked for Implementation)

1. **Antigravity builder:** Drop `paddock-builder.md`; build deterministic `platforms/antigravity/workflows/build_plan.py` (`run_workflow`) using `paddock-*` custom agents (`subagent: true`, `mainAgent: false`) and JSON-schema `finish` contracts.
2. **Worktrees:** Keep explicit `.worktrees/<slug>` (via `.git/info/exclude`) for PR delivery branches; treat Antigravity `workspace="share"` as optional for parallel isolated sub-tasks on already-git-initialized repos (noting `workflow-*` branch/worktree persistence outside workspace).
3. **Antigravity hooks (`agy_hooks.py`):**
   - Keep `guard` and `search-guard` in `rules/AGENTS.md` + Plan Mode (not `PreToolUse` on `edit_file`, since `run_workflow` agents share `USER_INPUT` / `lastUserInput` with root).
   - Enforce `branch-guard`, `plan-gate` (reading `transcriptPath` directly for latest `USER_EXPLICIT` approval timestamp — no `PreInvocation` hook needed), `overwrite` trap injection on `invoke_subagent.Prompt`, and `SessionStart` injection.
   - Always decode stdin via `sys.stdin.buffer.read().decode("utf-8")`, catch all exceptions and exit `0` (hooks fail closed otherwise), and avoid unlocked concurrent file writes.
4. **Shared `paddock-mem` MCP (`core/memory/mcp_server.py`):** Zero third-party deps, strict UTF-8 LF binary framing, handle `server/discover` (`-32601`), support `"2025-11-25"` + `"2025-03-26"`, log only to stderr, and resolve repos from explicit `cwd` argument.
