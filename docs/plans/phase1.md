# Paddock Phase 1 Implementation Plan (MVP: `core` + Antigravity + Claude Code)

Date: 2026-11-23
Status: Pending User Approval (Gate 2)
References: [paddock-spec.md](../specs/paddock-spec.md) (v2), [phase1-spike-results.md](../specs/phase1-spike-results.md)

---

## Locked Architecture Decisions (Incorporating Spikes 1–3)

1. **Self-contained plugin syncing (`scripts/sync.py`):** Sync both `core/skills/*` into `platforms/{antigravity,claude}/skills/` (preserving each platform's own `orchestrating/`) and `core/memory/{memory.py,mcp_server.py}` into `platforms/{antigravity,claude}/scripts/paddock_core/`, enforced by `tests/test_sync_drift.py`. Installed plugins under `~/.gemini/config/plugins/paddock/` and `~/.claude/plugins/cache/` stay completely self-contained with zero `../../` escapes or git symlinks.
2. **`PADDOCK_HOME` root semantics:** `PADDOCK_HOME` overrides `~/.paddock` directly (`<PADDOCK_HOME>/memory/` and `<PADDOCK_HOME>/projects/<slug>/memory/`), defaulting to `Path.home() / ".paddock"`.
3. **Hook & MCP launcher:** Use `uv run --no-project --script` across Windows and Linux (`137.19 ms` host warm median < `300 ms` target in Spike 1(d); verified in both Antigravity and Claude Code in Spike 3). Every hook script decodes stdin via `sys.stdin.buffer.read().decode("utf-8")` (Spike 1(a) Windows encoding fix) and is strictly fail-open (`try/except` -> log to stderr + emit valid allow JSON + exit `0`, since Antigravity fails closed on non-zero hook exits).
4. **Antigravity builder & worktrees (Spike 2):** Drop `paddock-builder.md`; implement deterministic `platforms/antigravity/workflows/build_plan.py` (`run_workflow`) using plugin-bundled `paddock-*` agents (`subagent: true`, `mainAgent: false`) and JSON-schema `finish` verdicts. Keep explicit `.worktrees/<slug>` (via `.git/info/exclude`) for PR delivery branches; allow an optional `workspace="share"` workflow argument for isolated parallel sub-tasks on pre-existing git repos.
5. **Antigravity guards & approval gates (Spike 1):** Keep `guard` and `search-guard` enforced by `rules/AGENTS.md` + native Plan Mode (because `run_workflow` agents share `USER_INPUT` / `lastUserInput` with root). Enforce `branch-guard`, `plan-gate` (reading `transcriptPath` directly for the latest `USER_EXPLICIT` `USER_INPUT` `created_at` — zero state file, handles repeated `"ok"`, avoids per-invocation `PreInvocation` overhead), `overwrite` trap injection on `invoke_subagent.Prompt`, `SessionStart` dual-layer index injection, and `Stop` standalone subagent evidence checks.
6. **Shared `paddock-mem` stdio MCP server (Spike 3):** Zero third-party dependencies (Python 3.12+ stdlib only), strict UTF-8 LF binary frames (`sys.stdout.buffer`), JSON-RPC `-32601` on `server/discover`, protocol versions including `"2025-11-25"` and `"2025-03-26"`, stderr-only logs, and repository resolution strictly from the explicit `cwd` argument (since Antigravity launches plugin MCP servers with `cwd=PLUGIN_ROOT`).

---

## Ordered Tasks

### Task 0 — Feature Worktree & Branch Setup
- **Agent:** `implementer` (`Mode: git`)
- **Files:** `.git/info/exclude` (local only; never tracked `.gitignore`), `.worktrees/phase1-mvp`
- **Scope:** Commit approved `docs/specs/phase1-spike-results.md` and `docs/plans/phase1.md` on `main` (or copy untracked into worktree), add `.worktrees/` to `.git/info/exclude`, and create worktree `.worktrees/phase1-mvp` on branch `feat/phase1-mvp`.
- **Acceptance:** `git worktree list` shows `.worktrees/phase1-mvp` on `feat/phase1-mvp`, tracked `.gitignore` contains no `.worktrees/` entry, and `git status --short` is clean inside the worktree.

### Task 1 — Unified Memory Engine (`core/memory/memory.py`) & Unit Tests
- **Agent:** `implementer`
- **Files:** `core/memory/memory.py`, `tests/test_memory_core.py`
- **Scope:** Pure Python 3.12+ stdlib module + CLI (`# /// script`): `PADDOCK_HOME` resolution; git-common-dir repo slug shared across worktrees (with non-git resolved-path fallback); zero-dep YAML frontmatter parser/serializer (2-space nested `metadata:`); strict kebab-case filename validation and path containment inside `memory/`; all 3 Foreman bug fixes (`refresh_layer()` returning `(problems, notices)` separately for `MEMORY.md.bak`, exact-path `deprecate()` matching without substring collision, clean 3,000-character cap truncation without leading `; … N more`); atomic writes (`tempfile` + `os.replace`, UTF-8 LF); `select()` (project layer first, recursive `**` path globs, topics, type, `traps_only`, match reasons), `write_entry()` (pre-write validation + dangling `[[link]]` warnings), `validate()`, `stale()`, `deprecate()` (rewriting `[[links]]`), `index()`, and read-only `import-claude [--dry-run] [--cwd <repo>] [--overwrite]`.
- **Acceptance:** `python -m pytest -q tests/test_memory_core.py` passes with regression coverage for all 3 Foreman bugs, path traversal rejection, worktree slug parity, and read-only `import-claude`.

### Task 2 — Stdlib `paddock-mem` MCP Server (`core/memory/mcp_server.py`) & Tests
- **Agent:** `implementer`
- **Files:** `core/memory/mcp_server.py`, `tests/test_mcp_server.py`
- **Scope:** Zero-dependency Python 3.12+ stdlib JSON-RPC 2.0 stdio server (`# /// script`, server name `paddock-mem`): binary `sys.stdin.buffer`/`sys.stdout.buffer` with strict LF `b"\n"` and flush; stderr-only logging; `-32601` for `server/discover`; protocol versions `2024-11-05`, `2025-03-26`, `2025-06-18`, `2025-11-25`; silent on notifications; `ping`, `tools/list`, and `tools/call` for all 6 tools (`mem_select`, `mem_write`, `mem_validate`, `mem_stale`, `mem_deprecate`, `mem_index`), resolving repository strictly from the explicit `cwd` argument and validating atomically before writes.
- **Acceptance:** `python -m pytest -q tests/test_mcp_server.py` passes real subprocess Windows stdio tests verifying zero `b"\r"` bytes, `server/discover` handling, silent notifications, all 6 tools, path-traversal denial, atomic invalid-write no-op, and explicit `cwd` resolution from an unrelated process cwd.

### Task 3 — Canonical Loop Contract & Agnostic Skills
- **Agent:** `generalist`
- **Files:** `core/specs/loop-contract.md`, `core/skills/antipatterns/SKILL.md`, `core/skills/verifying/SKILL.md`, `core/skills/releasing/SKILL.md`, `core/skills/memory/SKILL.md`
- **Scope:** Author canonical two-lane loop contract (bug `Mode: diagnose`, Approval Gates 1 & 2, typed verdicts, retry caps, Impeccable soft-dependency exit codes `0`/`2`/`1`, `Mode: git`, `PADDOCK_SOLO=1`) and all 4 platform-agnostic skills referencing `~/.paddock/` and `paddock-mem` MCP tools, free of Claude-specific tool names (`Agent`, `Grep`, `Glob`, `WebFetch`, `superpowers:*`, `~/.claude/`).
- **Acceptance:** All 5 files exist, all 4 `SKILL.md` files have valid frontmatter (`name`, `description`), and automated inspection confirms zero occurrences of `~/.claude`, `superpowers:`, or Claude-only tool names in `core/skills/`.

### Task 4 — Sync Script, Installer & Drift Tests
- **Agent:** `implementer`
- **Files:** `scripts/sync.py`, `scripts/install.py`, `tests/test_sync_drift.py`
- **Scope:** `scripts/sync.py` copies `core/skills/*` into `platforms/{antigravity,claude}/skills/` (leaving `orchestrating/` untouched) and `core/memory/{memory.py,mcp_server.py}` into `platforms/{antigravity,claude}/scripts/paddock_core/` using UTF-8 LF without git symlinks, plus `--check` drift mode. `scripts/install.py` installs/uninstalls selected platforms (`--platform antigravity|claude|all`, `--dry-run`, `--uninstall`) safely on Windows and Linux without editing `~/.gemini/config/config.json`. `tests/test_sync_drift.py` enforces parity across synced skills and `paddock_core/` files.
- **Acceptance:** `python -m pytest -q tests/test_sync_drift.py` passes (including positive drift-detection test on a temporary copy) and `python scripts/sync.py --check` exits `0` after adapters are synced.

### Task 5 — Antigravity Adapter (`platforms/antigravity/`) & Tests
- **Agent:** `implementer`
- **Files:** `platforms/antigravity/plugin.json`, `platforms/antigravity/mcp_config.json`, `platforms/antigravity/hooks.json`, `platforms/antigravity/rules/AGENTS.md`, `platforms/antigravity/agents/paddock-{generalist,implementer,ui,planner,verifier}.md`, `platforms/antigravity/skills/orchestrating/SKILL.md`, `platforms/antigravity/scripts/agy_hooks.py`, `platforms/antigravity/workflows/build_plan.py`, `tests/test_agy_hooks.py`, `tests/test_agy_workflow.py`
- **Scope:** Build complete native Antigravity plugin: strict `plugin.json`; `mcp_config.json` (`paddock-mem` via `scripts/paddock_core/mcp_server.py`); root `hooks.json` (`SessionStart`, `PreToolUse` `branch-guard` + `plan-gate` + `overwrite` trap injection, `Stop` standalone evidence check); `agy_hooks.py` (binary UTF-8 stdin, fail-open, `PADDOCK_SOLO=1`, `transcriptPath` approval-gate mtime check, `overwrite` on `invoke_subagent.Prompt`); plain-markdown `rules/AGENTS.md` (with `guard`/`search-guard` discipline + Plan Mode); 5 `paddock-*` custom agents (`subagent: true`, `mainAgent: false`, exact spec tool allowlists); `orchestrating/SKILL.md`; deterministic `workflows/build_plan.py` (factored for unit testing with mocked `agent()`: plan parser, `memory.select()` trap injection, JSON-schema `finish` verdicts, retry caps 2x `impl` -> `plan`, 2x `plan` per task -> escalate, `DISPUTE` once per task, `FAIL: env` uncapped + 1 flaky rerun, `FAIL: spec` immediate stop, configurable model tiers and `workspace` arg defaulting to `"inherit"`).
- **Acceptance:** `python -m pytest -q tests/test_agy_hooks.py tests/test_agy_workflow.py` passes covering all camelCase hook contracts, UTF-8 non-ASCII stdin, approval gates (including repeated `"ok"`), `overwrite`, `PADDOCK_SOLO=1`, fail-open behavior, and every `build_plan.py` routing/retry/cap/`DISPUTE` branch.

### Task 6 — Claude Code Adapter (`platforms/claude/` + `.claude-plugin/marketplace.json`) & Tests
- **Agent:** `implementer`
- **Files:** `.claude-plugin/marketplace.json`, `platforms/claude/.claude-plugin/plugin.json`, `platforms/claude/.mcp.json`, `platforms/claude/agents/{generalist,implementer,ui,planner,builder,verifier}.md`, `platforms/claude/skills/orchestrating/SKILL.md`, `platforms/claude/hooks/hooks.json`, `platforms/claude/scripts/claude_hooks.py`, `tests/test_claude_hooks.py`
- **Scope:** Build complete native Claude Code plugin: local marketplace pointing to `./platforms/claude`; `.mcp.json` using `${CLAUDE_PLUGIN_ROOT}/scripts/paddock_core/mcp_server.py`; 6 frontmatter-pinned agents (`haiku`/`low` generalist; `sonnet`/`medium` implementer/ui/planner/builder/verifier); dual POSIX `command` + Windows `commandWindows` `hooks/hooks.json`; `claude_hooks.py` implementing `guard`, `search-guard`, `branch-guard`, `plan-gate` (modes + User Approval Gate mtime checks via `CLAUDE_PLUGIN_DATA/prompts.json`), `trap-inject`, `plan-record`, `prompt-record`, `session-start` (orchestrator rules + both `~/.paddock/` layers + notices), `subagent-start` (`VERIFY_RULES` + trap index), `evidence-check` (`SubagentStop` with `stop_hook_active`), binary UTF-8 stdin, fail-open exit `0`, and `PADDOCK_SOLO=1`.
- **Acceptance:** `python -m pytest -q tests/test_claude_hooks.py` passes covering all 6 lifecycle events, approval-gate mtime checks, dual-layer `SessionStart`, `SubagentStop` evidence validation, `PADDOCK_SOLO=1`, and fail-open behavior.

### Task 7 — Sync, Top-Level Docs & Full End-to-End Verification
- **Agent:** `generalist` + `verifier`
- **Files:** `README.md`, `CHANGELOG.md`, synced copies under `platforms/{antigravity,claude}/skills/` and `platforms/{antigravity,claude}/scripts/paddock_core/`
- **Scope:** Run `python scripts/sync.py`, write `README.md` (overview, layout, install/sync/test, `PADDOCK_HOME`, `PADDOCK_SOLO=1`) and Keep a Changelog 1.1.0 `CHANGELOG.md`, then run full verification (`python -m pytest -q`, `python scripts/sync.py --check`, live Windows stdio smoke check of `core/memory/mcp_server.py` covering all 6 tools).
- **Acceptance:** All 6 test suites pass (`python -m pytest -q` exits `0`), `python scripts/sync.py --check` exits `0`, live MCP smoke test passes all 6 `mem_*` tools, and verifier returns `PASS` with quoted command outputs.
