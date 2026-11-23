# Paddock — Multi-Platform Orchestration & Unified Memory Spec

Date: 2026-11-23
Status: Approved (v2) — ready for Phase 1 spikes in the new `paddock` workspace.
Origin: Inherited from `foreman` (v0.2.3); `foreman` remains untouched.

---

## 1. Intent & Core Principles

**Paddock** is a personal-use, single-monorepo orchestration and typed-memory suite supporting five AI coding platforms: **Claude Code**, **Antigravity**, **Codex**, **GitHub Copilot**, and **Pi**.

### Core Principles

1. **Native-first, empirically evaluated:** Start with each platform's native primitives (workflows, workspace isolation, hooks, MCP, glob rules, JSON schemas). Evaluate them in real runs against custom Paddock optimizations on reliability, latency, and token cost — keep native where it wins, optimize custom where native falls short.
2. **Foreman orchestration loop:** Preserve the core discipline that keeps work bounded and verifiable:
   - Keep the main session as a pure orchestrator (clarify, write specs, review plans, judge escalations; never burn main context on bulk reads or code edits).
   - Sort every request into a **Quick lane** or a **Plan lane**; bugs always start with `Mode: diagnose` (failing regression test + root cause).
   - Enforce **user approval gates** after spec creation and after plan creation before build execution starts.
   - Route every verifier verdict (`PASS`, `FAIL: impl`, `FAIL: plan`, `FAIL: spec`, `FAIL: env`) to the cheapest step that can fix it under hard retry caps.
3. **Model-tier fitting:** Route every role and task to the lowest model tier that can complete it reliably:
   - **Fast / lightweight tier** (`haiku`, `flash_lite`): lookups (`Mode: lookup`), summaries, doc/text edits, exact mechanical edits.
   - **Mid / workhorse tier** (`sonnet`, `flash` / `pro` as tuned): coding, tests, UI, planning, building, verification, git writes.
   - **Flagship tier** (main session): clarifying ambiguity with the user, writing specs, reviewing plans, handling `spec`/`env`/capped escalations.
4. **One shared core, five clean platform adapters:** Keep platform-agnostic logic (memory engine, MCP server, engineering skills, loop contract, tests) in `core/`. Keep each platform's manifests, agents, hooks, and orchestration code isolated under `platforms/<name>/` to prevent collisions.
5. **One unified private brain (`~/.paddock/`):** All five platforms share one local user and project memory store without polluting target git repositories.
6. **Fewer unnecessary handoffs:** Eliminate LLM middleware layers where a platform supports deterministic code orchestration (e.g., Antigravity `run_workflow` replaces the prompt-driven `builder` agent).
7. **Windows-first, Linux-ready portability:** Develop and validate on Windows first (no git symlinks, `cmd /c`-safe Windows hook invocations, `pathlib` paths), while keeping all `core/` Python modules, MCP transport, sync scripts, and path/EOL handling OS-agnostic so Linux works cleanly when enabled (POSIX `sh -c` hook entries alongside Windows entries, forward-slash relative paths, UTF-8 LF output).

### Delivery Phases

- **Phase 1 (MVP):** `core/` (memory engine + zero-dependency stdio MCP server + agnostic skills + sync/drift script + test suite) + **`antigravity`** adapter + **`claude`** adapter, verified end-to-end.
- **Phase 2:** **`codex`**, **`copilot`**, and **`pi`** adapters built on the stabilized `core/`.

---

## 2. Monorepo Layout

```text
paddock/
├── .claude-plugin/
│   └── marketplace.json            # Local Claude marketplace -> source: "./platforms/claude"
├── package.json                    # Pi package manifest ("pi" field -> platforms/pi/...)
├── core/
│   ├── memory/
│   │   ├── memory.py               # Typed memory model, parser, validator, indexer, CLI, importer
│   │   └── mcp_server.py           # Zero-dependency Python stdlib JSON-RPC 2.0 stdio MCP server
│   ├── skills/                     # Platform-agnostic source of truth (synced into adapters)
│   │   ├── antipatterns/SKILL.md
│   │   ├── memory/SKILL.md
│   │   ├── releasing/SKILL.md
│   │   └── verifying/SKILL.md
│   └── specs/
│       └── loop-contract.md        # Canonical two-lane loop, approval gates, verdicts, retry caps
├── platforms/
│   ├── antigravity/                # Phase 1 — native Antigravity plugin
│   │   ├── plugin.json
│   │   ├── mcp_config.json
│   │   ├── hooks.json
│   │   ├── rules/AGENTS.md
│   │   ├── agents/                 # paddock-{generalist,implementer,ui,planner,verifier}.md
│   │   ├── skills/                 # orchestrating/ + synced core skills
│   │   ├── workflows/
│   │   │   └── build_plan.py       # Deterministic run_workflow builder
│   │   └── scripts/
│   │       └── agy_hooks.py
│   ├── claude/                     # Phase 1 — native Claude Code plugin
│   │   ├── .claude-plugin/plugin.json
│   │   ├── .mcp.json
│   │   ├── agents/                 # generalist, implementer, ui, planner, builder, verifier (.md)
│   │   ├── skills/                 # orchestrating/ + synced core skills
│   │   ├── hooks/hooks.json
│   │   └── scripts/
│   │       └── claude_hooks.py
│   ├── codex/                      # Phase 2 — OpenAI Codex CLI plugin
│   │   ├── plugin.json             # schema_version + extensions.com.openai
│   │   ├── mcp.json
│   │   ├── AGENTS.md
│   │   ├── agents/                 # *.toml subagent definitions
│   │   ├── hooks.json              # 10 native PascalCase lifecycle events
│   │   ├── skills/                 # orchestrating/ + synced core skills
│   │   └── scripts/
│   │       └── codex_hooks.py
│   ├── copilot/                    # Phase 2 — GitHub Copilot Agent Plugin 1.0
│   │   ├── plugin.json
│   │   ├── mcp.json
│   │   ├── agents/                 # *.agent.md custom agents
│   │   ├── instructions/           # *.instructions.md (applyTo globs)
│   │   ├── hooks/hooks.json        # camelCase lifecycle events
│   │   ├── skills/                 # orchestrating/ + synced core skills
│   │   └── scripts/
│   │       └── copilot_hooks.py
│   └── pi/                         # Phase 2 — Pi TypeScript extension package
│       ├── extensions/             # In-process TS guards, branch check, TUI status, mem tools
│       └── skills/                 # orchestrating/ + synced core skills
├── scripts/
│   ├── sync.py                     # Syncs core/skills into each platform (no git symlinks)
│   └── install.py                  # Personal-machine helper to install/link selected platforms
├── tests/                          # pytest suite (core, MCP, hooks, workflow state machine, drift)
├── docs/
│   ├── specs/
│   └── plans/
├── CHANGELOG.md
└── README.md
```

### Why `scripts/sync.py` Instead of Symlinks

Git symlinks frequently break on Windows without Developer Mode and `core.symlinks=true`, while still needing to work seamlessly on Linux. Keeping `core/skills/` as the single source of truth, syncing copies with `scripts/sync.py`, and enforcing parity with `tests/test_sync_drift.py` works identically on both Windows and Linux.

---

## 3. Shared Core (`core/`)

### 3.1 Unified Memory Engine (`core/memory/`) — Option 1

All five platforms share one local store on the user's machine:

- **User layer:** `~/.paddock/memory/` (`user`, `feedback`, global `reference` / traps) + generated `MEMORY.md`
- **Project layer:** `~/.paddock/projects/<repo-slug>/memory/` (`project`, project `reference` / traps) + generated `MEMORY.md`
- **Override:** `PADDOCK_HOME` environment variable replaces `~` (for tests and isolation).
- **Repo slug:** Derived from `git rev-parse --path-format=absolute --git-common-dir` (main checkout root, shared across all git worktrees).

#### Entry Format (Inherited from Foreman)

One fact per `<kebab-case-name>.md`, YAML frontmatter with a 2-space nested `metadata:` block:

```markdown
---
name: wsl-stops-postgres
description: one line used for recall and MEMORY.md index
metadata:
  type: user | feedback | project | reference
  trap: true                      # optional; costly gotchas
  paths: [scripts/dev_db.ps1, tests/**]
  topics: [wsl, postgres]
  status: active | deprecated
  superseded_by: other-name       # optional, when deprecated
  verified: human | model
  verified_on: 2026-11-23
  stale_after: 2027-05-23 | never # required for project and reference
---
Body text. Traps use **Symptom:**, **Cause:**, **Do instead:**.
Link entries with [[name]].
```

#### Foreman Memory Bugs Fixed in Paddock

1. **`MEMORY.md.bak` false error:** `refresh_layer()` returns `(problems, notices)` separately so backing up hand-written `MEMORY.md` lines is never reported as an invalid memory entry.
2. **`deprecate` substring false exit-1:** Match the exact file path of the deprecated entry rather than substring matching `p.name in x`.
3. **Truncation prefix bug:** When the first problem exceeds the 3,000-character session-start cap, truncate cleanly without emitting a leading `; … N more`.

#### One-Time Read-Only Importer

`uv run --no-project --script core/memory/memory.py import-claude [--dry-run] [--cwd <repo>]`:
- Copies existing entries from `~/.claude/memory/` and `~/.claude/projects/<slug>/memory/` into `~/.paddock/`.
- Never modifies or deletes `~/.claude/` files; skips existing destination names unless `--overwrite` is passed; regenerates `~/.paddock/` indexes afterward.

#### Shared Stdio MCP Server (`core/memory/mcp_server.py`)

Implemented with **Python 3.12 stdlib only** (JSON-RPC 2.0 over stdin/stdout, zero third-party packages) so every platform can launch it directly via `uv run --no-project --script` on both Windows and Linux with fast cold start and no virtualenv setup.

Server name: **`paddock-mem`** (prefixed to avoid global MCP name collisions).

| MCP Tool | Purpose |
| :--- | :--- |
| `mem_select(cwd, paths?, topics?, type?, traps_only?)` | Returns matching active entries (project layer first) with match reasons and full trap bodies. |
| `mem_write(cwd, layer, name, description, metadata, body)` | Validates frontmatter **before** writing, writes atomically inside `~/.paddock/` only (rejects path traversal), refreshes `MEMORY.md`, returns dangling `[[link]]` warnings. |
| `mem_validate(cwd)` | Validates both layers and returns structured problems list. |
| `mem_stale(cwd)` | Lists active entries past `stale_after`. |
| `mem_deprecate(cwd, name, superseded_by?)` | Marks entry deprecated, rewrites `[[links]]`, refreshes index. |
| `mem_index(cwd)` | Regenerates both `MEMORY.md` indexes and returns `(problems, notices)`. |

### 3.2 Agnostic Skills (`core/skills/`)

Cleaned of Claude-specific tool names (`Agent`, `Grep`, `superpowers:*`, `~/.claude/`):
- **`antipatterns`:** Scope & simplicity, code smells, GoF discipline, MVC layering, agent workflow mistakes.
- **`verifying`:** Observed-this-session evidence rule (`command + output`, `file:line`, or `URL + quote`), `verified: human` memory rule, `## Evidence` / `## Unverified` contract.
- **`releasing`:** SemVer 2.0.0, Keep a Changelog 1.1.0, Conventional Commits 1.0.0, GitHub Flow, worktree-first (`.worktrees/<slug>` via `.git/info/exclude`), per-worktree `codegraph` CLI rules, PR merge (`gh pr merge --merge`) + cleanup order, never push tags, no size exemptions.
- **`memory`:** Entry schema, fitness test, verification rules, `paddock-mem` MCP usage.

### 3.3 Canonical Orchestration Contract (`core/specs/loop-contract.md`)

Inherited from Foreman Loop v2 + User Approval Gates + Git Dispatch Gate:

1. **Bug intake:** Never fix ad hoc. Dispatch `implementer` in `Mode: diagnose` first (failing regression test + root cause `file:line`).
2. **Two-lane sort:**
   - **Quick lane:** One dispatch, ≤ ~3 known files, no open decisions, no auth/secrets/migrations/public API, one check command. Two `FAIL: impl` or `BLOCKED` promotes to plan lane.
   - **Plan lane:** Orchestrator writes spec (with Security + optional UI Design) → **STOP for user approval** → `planner` writes `docs/plans/<file>.md` (every task has `Agent:`, `Files:`, acceptance) → orchestrator reviews and **STOPS for user approval** → execute plan → return one summary with quoted verifier evidence.
3. **Typed verdicts & cheapest-fixer routing:**
   - `PASS` → next task
   - `FAIL: impl` → same agent (2 fails → escalate to `FAIL: plan`)
   - `FAIL: plan` / `BLOCKED: plan` → `planner` revises only affected tasks (2 revisions of same task → orchestrator → user)
   - `FAIL: spec` → orchestrator → user
   - `FAIL: env` → orchestrator (uncapped; flaky test rerun once first)
   - `DISPUTE` → allowed once per task with evidence (counts as one attempt)
4. **Optional Impeccable integration:** Soft dependency. UI tasks load `impeccable` if installed and run `npx impeccable detect --json <target>` (exit 0 pass, exit 2 `FAIL: impl`, exit 1 `FAIL: env`; if missing, record under Unverified and relay once per session).
5. **Git write discipline:** Main session runs read-only git commands; git writes run through `Mode: git` (intent only, never dictated commit messages, PR merge + cleanup order, no size exemptions).
6. **Escape hatch:** `PADDOCK_SOLO=1` disables enforcement hooks for solo sessions.

---

## 4. Phase 1 Platform Adapters

### 4.1 Antigravity Adapter (`platforms/antigravity/`)

#### Request Intake, Spec Authoring, and Handoff Flow

**Who analyzes the request and creates the spec?** **The main Antigravity session (the Orchestrator)** — not a custom subagent.

- Subagents (`invoke_subagent` / `run_workflow`) cannot hold an interactive multi-turn design conversation with the user; delegating spec writing to a subagent turns open product decisions into guesses.
- Antigravity's main session already runs your flagship model with native **Plan Mode**, `ask_question`, and **User-Facing Artifacts (`RequestFeedback: true`)**.
- To protect main-session context during discovery, the Orchestrator queries `mem_select`, uses `read_file` / `codegraph` only for known targets, and offloads broad codebase or web exploration to `paddock-generalist` (`Mode: lookup`, `model="flash_lite"`).

End-to-end Plan Lane flow on Antigravity:

```text
User Request
  └─► Main Session (Orchestrator — flagship model)
        1. Calls mem_select; dispatches paddock-generalist (Mode: lookup, flash_lite) if discovery needed
        2. Clarifies open requirements via ask_question; writes docs/specs/<slug>.md (Security + UI Design)
        3. Presents spec (RequestFeedback: true) ──► [USER APPROVAL GATE 1]
        4. Invokes paddock-planner via invoke_subagent with approved spec path
  └─► paddock-planner (mid-tier) writes docs/plans/<slug>.md (Agent:, Files:, Acceptance per task)
  └─► Main Session (Orchestrator)
        5. Reviews plan vs. spec; presents plan ──► [USER APPROVAL GATE 2]
        6. Runs workflows/build_plan.py via run_workflow
  └─► build_plan.py (deterministic Python workflow)
        7. Executes tasks (paddock-implementer / paddock-ui / paddock-generalist) + paddock-verifier
           with JSON-schema verdicts, exact retry caps, and FAIL: plan calls to paddock-planner
        8. Returns single structured summary + quoted verifier evidence ──► Main Session reports done
```

#### Adapted Foreman Concepts — Detailed Breakdown

Each Foreman mechanism is mapped to Antigravity's native primitive first, with clear criteria for evaluating whether native outperforms custom:

1. **Prompt-driven `builder.md` → Deterministic `workflows/build_plan.py` (`run_workflow`):**
   - *Foreman:* A Sonnet `builder` subagent reads the plan and follows retry caps, `DISPUTE` limits, and verdict routing through prose instructions (Foreman's spec explicitly accepts that hooks cannot track nested builder state cheaply).
   - *Antigravity adaptation:* A Python `run_workflow` script parses `docs/plans/<file>.md`, calls `memory.select()` for each task's `Files:`, invokes `agent(type_name="paddock-...", model=..., schema=...)` in `pipeline()` or `parallel()`, and implements the exact routing and retry-cap state machine in Python. Eliminates one full LLM orchestration layer.
   - *Evaluate:* Confirm in Spike 2 that plugin-bundled `type_name` agents work cleanly inside `run_workflow` and measure handoff overhead vs. a single long-lived subagent.
2. **`SubagentStop` regex hook → JSON Schema `finish` contract:**
   - *Foreman:* A `SubagentStop` hook (`evidence-check`) reads the subagent's transcript and regex-checks for `## Evidence`, `## Unverified`, and exactly one verifier verdict line, bouncing the agent back once on failure.
   - *Antigravity adaptation:* Inside `run_workflow`, every `agent(..., schema=...)` call enforces a JSON Schema via Antigravity's `finish` tool (`verdict`, `summary`, `evidence`, `unverified`). The runtime rejects missing fields or invalid verdict enums automatically with zero regex parsing. For standalone `invoke_subagent` calls (quick lane / diagnose), fall back to `Stop` hook checking `continuationActive` (one retry max).
   - *Evaluate:* Confirm schema retries inside `run_workflow` are cleaner and cheaper than prose + `Stop` hook retries.
3. **Static agent frontmatter `model` → Dynamic dispatch `model` tiers:**
   - *Foreman:* Each `agents/*.md` pins `model: haiku|sonnet` and `effort: low|medium` in YAML frontmatter, so picking the agent picks the model.
   - *Antigravity adaptation:* Antigravity custom `agents/*.md` files drop `model` frontmatter. Instead, `build_plan.py` and the Orchestrator pass `model` dynamically at dispatch time: `"flash_lite"` for `paddock-generalist` (lookups, docs, mechanical edits) and `"flash"` or `"pro"` for `paddock-implementer`, `paddock-ui`, `paddock-planner`, and `paddock-verifier` (configurable via workflow `Args`). For multi-worker parallel exploration, `swarm` can also pin exact model names.
   - *Evaluate:* Benchmark `"flash"` vs. `"pro"` quality on implementation and verification tasks to tune default tier assignments.
4. **Manual `.worktrees/` plumbing → Native `workspace="share"` vs. explicit PR worktrees:**
   - *Foreman:* Every brief carries `Worktree:` and `Branch:`; task 0 or quick-lane agent runs `git worktree add .worktrees/<slug>`, copies untracked specs/plans, runs `codegraph init -y`, and cleans up after PR merge.
   - *Antigravity adaptation:* Antigravity natively supports `workspace="share"` (worktree-like shared git repo) and `workspace="branch"` (isolated clone) on `invoke_subagent`, `run_workflow`, and `swarm`. Use `workspace="share"` for parallel/isolated task execution where no persistent PR branch is needed yet, and test in Spike 2 how `workspace="share"` interacts with feature branches, `codegraph`, and `gh pr create`.
   - *Evaluate:* If `workspace="share"` cleans up automatically on agent exit (losing the branch before PR merge), keep `workspace="share"` for read/parallel sub-tasks and use explicit `.worktrees/<slug>` for PR delivery branches.
5. **User Approval Gates (`prompt-record` hook) → Native Plan Mode + `RequestFeedback` Artifacts (+ mtime gate):**
   - *Foreman:* A `UserPromptSubmit` hook timestamps the user's last turn in `prompts.json`; `plan_gate` denies dispatching `planner` or `builder` if the spec or plan file was edited after that timestamp.
   - *Antigravity adaptation:* Antigravity provides native Plan Mode (`exit_plan_mode`) and User-Facing Artifacts with `RequestFeedback: true` (rendering an explicit Proceed/feedback gate in the UI). Pair that native UX with a `PreToolUse` mtime check in `agy_hooks.py` (recording user turns via `PreInvocation` when `lastUserInput` changes) so approval is enforced mechanically as well as visually.
   - *Evaluate:* Verify in Spike 1 whether `PreInvocation`'s `lastUserInput` reliably detects a fresh user turn without false denials.
6. **`SubagentStart` + `trap-inject` → `PreToolUse` `overwrite` + `build_plan.py` injection:**
   - *Foreman:* `SubagentStart` injects `VERIFY_RULES` + trap titles; `PreToolUse` `trap-inject` injects full trap text via `additionalContext` when a subagent edits a matching file.
   - *Antigravity adaptation:* Antigravity has no `SubagentStart` event and `PreToolUse` does not support `additionalContext` — instead, `PreToolUse` natively supports **`overwrite`** (mutating tool call arguments before execution). A `PreToolUse` hook on `invoke_subagent` reads file paths from `Prompt`, queries `core/memory`, and appends matched traps + `VERIFY_RULES` into `Prompt` via `overwrite`. Inside `build_plan.py`, Python calls `memory.select()` directly before spawning each `agent()`.
   - *Evaluate:* Confirm in Spike 1 that `overwrite` on `invoke_subagent` works cleanly without confusing the model's replay notice; if noisy, rely on `build_plan.py` + `PreInvocation` `injectSteps`.
7. **`SessionStart` context injection → Native `SessionStart` `injectSteps`:**
   - *Foreman:* Returns `hookSpecificOutput.additionalContext` with `ORCHESTRATOR_RULES` and user `MEMORY.md` (relying on Claude auto-memory for project `MEMORY.md`).
   - *Antigravity adaptation:* `SessionStart` returns `{"injectSteps": [{"systemMessage": {"systemMessage": "..."}}]}` containing **both** user and project `~/.paddock/` `MEMORY.md` indexes, stale reminders, and concise orchestrator rules.
   - *Evaluate:* Confirm `SessionStart` token footprint stays tight (under 25 KB cap) and does not duplicate `rules/AGENTS.md`.
8. **`ORCHESTRATOR_RULES` → Plugin `rules/AGENTS.md`:**
   - *Foreman:* Injects rules text dynamically at `SessionStart`.
   - *Antigravity adaptation:* Antigravity plugins natively load `rules/AGENTS.md` as an always-on rule whenever the plugin is enabled, deduplicated automatically. Keep static discipline in `rules/AGENTS.md` and dynamic session state (memory indexes, stale entries) in `SessionStart`.
9. **`guard`, `branch-guard`, `plan-gate`, `search-guard` → Root `hooks.json` (`agy_hooks.py`):**
   - *Foreman:* All four run in `PreToolUse` and branch on `"agent_id" in payload` to separate main session vs. subagents.
   - *Antigravity adaptation:* Root `<plugin>/hooks.json` uses named hook groups (`{"paddock": {"SessionStart": [...], "PreToolUse": [...]}}`), camelCase stdin (`toolCall.name`, `toolCall.args`, `conversationId`), and `{"decision": "deny", "reason": "..."}` stdout.
     - `branch-guard` (`PreToolUse` on `edit_file|write_file`): works identically for all callers — blocks edits on `main`/`master` outside specs/plans/memory.
     - `plan-gate` (`PreToolUse` on `invoke_subagent|run_workflow`): enforces `Mode: quick|diagnose|git|lookup`, named plans with `Agent:`/`Files:`, `Mode: git` rules, and approval mtime.
     - `guard` (main-session no-edit) and `search-guard` (main-session no-`search_web`): **gated on Spike 1** because Antigravity's documented `PreToolUse` stdin does not include an `agent_id` field. If Spike 1 finds a reliable root-vs-child signal (via `conversationId` / `transcriptPath` / `SessionStart`), enable mechanical `guard`; otherwise keep `guard` enforced by `rules/AGENTS.md` + native Plan Mode so subagents are never blocked by mistake.
10. **`bin/mem` + `memory-write` hook → `paddock-mem` MCP server (`mcp_config.json`):**
    - *Foreman:* Places `bin/mem` on Bash `PATH` and validates writes via `PostToolUse` `decision: "block"`.
    - *Antigravity adaptation:* Antigravity plugins do not modify `PATH` and `PostToolUse` only returns `{}`. Registering `paddock-mem` in `mcp_config.json` gives every agent native `mem_*` tools with pre-write atomic validation and works cleanly inside Antigravity's terminal sandbox.

#### Antigravity Custom Agents (`platforms/antigravity/agents/`)

5 specialist subagents (`builder.md` omitted because `workflows/build_plan.py` replaces it; prefixed `paddock-*` to avoid colliding with Antigravity built-ins like `research`, `browser`, `self`):

- `paddock-generalist.md`: `subagent: true`, `mainAgent: false`, `tools: [read_file, edit_file, write_file, search_web]` (no `run_command`).
- `paddock-implementer.md`: `subagent: true`, `mainAgent: false`, `tools: [read_file, edit_file, write_file, run_command, manage_task, search_web]`.
- `paddock-ui.md`: same tools as implementer + `impeccable` soft-dependency guidance.
- `paddock-planner.md`: `subagent: true`, `mainAgent: false`, `tools: [read_file, write_file, edit_file, run_command]`.
- `paddock-verifier.md`: `subagent: true`, `mainAgent: false`, `tools: [read_file, run_command, manage_task]` (read-only; never edits).

---

### 4.2 Claude Code Adapter (`platforms/claude/`)

Preserves every proven mechanical guard from Foreman v0.2.3 while upgrading memory and approval gates:

1. **Orchestrator & Spec Authoring:** Main Claude Code session (Opus) clarifies, writes specs, stops for approval, reviews plans, stops for approval, and handles `spec`/`env`/capped escalations.
2. **6-Agent Frontmatter-Pinned Roster:** Keeps `generalist` (`haiku`, `low`), `implementer`, `ui`, `planner`, `builder`, and `verifier` (`sonnet`, `medium`) with explicit `model`, `effort`, `tools`, and `disallowedTools` (Claude Code has no Python `run_workflow`, so `builder.md` remains the right native choice here).
3. **Full Mechanical Hooks (`hooks/hooks.json` + `scripts/claude_hooks.py`):**
   - `PreToolUse`: `guard` (main session no-edit), `search-guard` (main session no-`Grep`/`Glob`/`WebFetch`/`WebSearch`), `branch-guard` (subagents off default branch), `plan-gate` (`Mode: quick|diagnose|lookup|git` + **User Approval Gates** mtime check on specs and plans), `trap-inject`.
   - `PostToolUse`: `plan-record` (records session plan files).
   - `UserPromptSubmit`: `prompt-record` (records user turn timestamps in `CLAUDE_PLUGIN_DATA/prompts.json` for approval gates).
   - `SessionStart`: injects orchestrator rules + **both** user and project `~/.paddock/` `MEMORY.md` indexes (replacing Claude auto-memory dependency) + stale/validation notices.
   - `SubagentStart`: injects `VERIFY_RULES` + trap index.
   - `SubagentStop`: `evidence-check` (`## Evidence`, `## Unverified`, single verifier verdict line).
   - Supports both POSIX `command` (Linux-ready) and `commandWindows` (Windows PowerShell).
4. **Shared `paddock-mem` MCP (`.mcp.json`):** Replaces `bin/mem` and the `PostToolUse` `memory-write` hook with the same zero-dependency Python stdio MCP server used by Antigravity.
5. **Distribution:** Repo root `.claude-plugin/marketplace.json` points `"source": "./platforms/claude"`.

---

## 5. Phase 2 Platform Adapters (Architecture Summary)

Detailed per-platform specs will be finalized after Phase 1 stabilizes `core/`:

1. **Codex (`platforms/codex/`):**
   - Root `plugin.json` (`schema_version: "v1"`, `extensions.com.openai`), `mcp.json` (`paddock-mem`), concise `AGENTS.md`.
   - Native `.toml` subagents with scoped worktree isolation.
   - `hooks.json` mapping Paddock guards onto Codex's 10 PascalCase lifecycle events (`SessionStart`, `SubagentStart`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `SubagentStop`, `Stop`) — near-1:1 mechanical parity with Claude Code.
2. **GitHub Copilot (`platforms/copilot/`):**
   - Agent Plugins 1.0 `plugin.json`, `mcp.json` (`paddock-mem`), `agents/*.agent.md` custom agents, `instructions/*.instructions.md` (`applyTo` globs), `skills/` (`agentskills.io`).
   - `hooks/hooks.json` using Copilot's camelCase events (`sessionStart`, `userPromptSubmitted`, `preToolUse`, `postToolUse`, `agentStop`).
3. **Pi (`platforms/pi/` + root `package.json`):**
   - Root `package.json` `"pi"` field points to `./platforms/pi/extensions` and `./platforms/pi/skills`.
   - **In-process TypeScript `extensions/*.ts`**: zero subprocess spawn latency for `guard`, `branch-guard`, `plan-gate`, `evidence-check`, and `SessionStart` injection; native TUI widget showing current lane/worktree/verdict; direct `mem_*` tools wrapping `core/memory` (compatible with `pi-mcp-adapter` too).
   - Composes with `pi-subagents` as a soft dependency (same pattern as Impeccable).

---

## 6. Pre-Implementation Spikes (Phase 1 Gate)

Before writing full Phase 1 adapter code, run three short throwaway spikes to empirically test Antigravity's native primitives:

1. **Spike 1 — Antigravity Hook Payload, Identity & Overwrite:** Inspect real stdin JSON sent to `SessionStart`, `PreToolUse`, and `PreInvocation` for root vs. `invoke_subagent` / `run_workflow` agents. Verify (a) whether root vs. child is distinguishable for `guard`, (b) whether `PreToolUse` `overwrite` cleanly mutates `invoke_subagent.Prompt`, (c) whether `PreInvocation` `lastUserInput` reliably drives approval-gate mtime checks, and (d) `uv run --no-project --script` hook latency under `cmd /c` (< 300 ms target).
2. **Spike 2 — Antigravity `run_workflow` Custom Agent & `workspace="share"`:** Run a 2-step workflow invoking `agent(type_name="paddock-verifier", schema=..., workspace="share")` to confirm plugin-bundled agent names resolve inside `run_workflow`, measure handoff cost, and test how `workspace="share"` interacts with git branches, `codegraph`, and PR creation.
3. **Spike 3 — Stdlib `paddock-mem` MCP Server:** Smoke-test pure-Python JSON-RPC 2.0 stdio handshake (`initialize`, `tools/list`, `tools/call`) inside both Antigravity (`mcp_config.json`) and Claude Code (`.mcp.json`) on Windows.

---

## 7. Security & Testing

### Security

- **Fail-open hooks:** Every hook catches all exceptions, logs to stderr, and exits 0. Never echo raw user prompts or secrets in deny messages.
- **Path containment in `mem_write`:** Reject entry names containing path separators, `..`, or non-kebab-case characters; verify resolved write path stays strictly inside `~/.paddock/memory/` or `~/.paddock/projects/<slug>/memory/`.
- **Git safety:** Never push tags (`--tags`, `--follow-tags` rejected by verifier), never edit tracked `.gitignore` for `.worktrees/`, never force-delete worktrees or branches (`--force`, `branch -D`) without user confirmation.

### Testing (`python -m pytest -q`)

- `tests/test_memory_core.py`: frontmatter parsing, validation, indexing, staleness, deprecation (including Foreman bug regression fixes), `import-claude` read-only safety.
- `tests/test_mcp_server.py`: JSON-RPC framing, all 6 `mem_*` tools, path-traversal rejection, atomic validation failure.
- `tests/test_sync_drift.py`: fails if any `platforms/*/skills/` copy differs from `core/skills/`.
- `tests/test_claude_hooks.py`: `guard`, `search-guard`, `branch-guard`, `plan-gate` (quick/diagnose/lookup/git + approval-gate mtime checks), `prompt-record`, `session-start` (both layers), `evidence-check`.
- `tests/test_agy_hooks.py`: camelCase `PreToolUse`/`SessionStart` contracts, `overwrite` trap injection, `branch-guard`, `plan-gate`.
- `tests/test_agy_workflow.py`: unit-tests `workflows/build_plan.py` task parsing, retry caps (2× `impl` → `plan`, 2× `plan` → escalate), `DISPUTE`, `FAIL: env` uncapped, `FAIL: spec` immediate stop using mocked `agent()`.

---

## 8. Confirmed Decisions (Locked)

1. **Naming & Isolation:** Confirmed — Antigravity custom agents prefixed `paddock-*`, MCP server named `paddock-mem`, escape env var `PADDOCK_SOLO=1`, unified memory root `~/.paddock/`.
2. **Antigravity Builder:** Confirmed conditionally — drop `builder.md` on Antigravity only if Spike 2 proves native `run_workflow` (`workflows/build_plan.py`) cleanly executes plans with plugin-bundled agents; otherwise fall back to `paddock-builder.md`. Keep `builder.md` on Claude Code.
3. **Shared MCP Memory (`paddock-mem`):** Confirmed — zero-dependency Python stdlib stdio MCP server replaces `bin/mem` and the `memory-write` hook on both Phase 1 platforms.
4. **User Approval Gates (Hard Requirement):** Confirmed as mandatory from Day 1 — both Phase 1 platforms must stop for explicit user approval after spec creation and after plan creation before any build executes.
