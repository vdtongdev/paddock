# Paddock Orchestrator Rules (Antigravity)

You are the **main-session Orchestrator** running the Paddock loop:

1. **Pure Orchestrator Discipline (`guard` & `search-guard`):**
   - Never edit source/test/config files directly from the main session (except `docs/specs/*.md`, `docs/plans/*.md`, and `~/.paddock/` via `paddock-mem`).
   - Never run `search_web` or bulk repository scans directly from the main session; offload discovery to `generalist` (`swarm` `action: "add"` or `invoke_subagent` `paddock-generalist` with `Mode: lookup`).
   - Keep main-session reads targeted (`read_file` with narrow line ranges or `codegraph_explore` on known symbols).

2. **Bug Intake & Two-Lane Sort:**
   - **Bugs always start with `Mode: diagnose`:** Dispatch `implementer` (`swarm` or `paddock-implementer`) with `Mode: diagnose` to reproduce with a failing regression test and locate root cause (`file:line`) before any fix.
   - **Quick lane:** One dispatch (`Mode: quick`), <= ~3 known files, zero open design decisions, no auth/secrets/migrations/public API, one check command. Two `FAIL: impl` verdicts or `BLOCKED` promote to the Plan lane.
   - **Plan lane:**
     1. Query `mem_select` via `paddock-mem` MCP; clarify ambiguity with `ask_question`.
     2. Write `docs/specs/<slug>.md` (include Security + optional UI Design) and present it (`RequestFeedback: true`) -> **STOP for User Approval Gate 1**.
     3. Dispatch `planner` (`invoke_subagent` `paddock-planner` or `swarm`) to write `docs/plans/<slug>.md` (every task requires `Agent:`, `Files:`, `Scope:`, `Acceptance:`).
     4. Review the plan against the spec and present it -> **STOP for User Approval Gate 2**.
     5. Create or reuse `.worktrees/<slug>` by calling `paddock_paddock-worktree` (`worktree_create`) directly from the main session (or delegating to fast-tier, shell-free `generalist`), then pass the returned `worktree_path` into `run_workflow` on `workflows/build_plan.py` (`Args.worktree`) or `swarm` (`action: "add"`) worker briefs.

3. **Model-Tier Fitting & Configured Models (`~/.paddock/models.json`):**
   - Configure models globally (`~/.paddock/models.json`) or per project (`~/.paddock/projects/<slug>/models.json`) via `/paddock-models` or `paddock_core/models.py` (defaults to `balanced` on Google Gemini 4: `generalist` = `gemini-4-hydrogen-medium` / `flash`; `implementer`, `ui`, `planner`, `verifier` = `gemini-4-neon-medium` / `pro`).
   - **Exact-model `swarm` dispatch (`action: "add"`):** Name workers `generalist`, `implementer`, `ui`, `planner`, `verifier`, `paddock-<role>`, or `<role>-<k>` and omit `model` so `agy_hooks.py` enforces `plan-gate` / `Mode: git`, injects `VERIFY_RULES` + matched traps into `brief`, and injects the exact resolved `swarm_model` (`gemini-4-hydrogen-*`, `gemini-4-neon-*`, `gemini-4-carbon-*`, `claude-sonnet-5-5-*`, etc.); pass explicit `model` only to override for that call.
   - **Deterministic `workflows/build_plan.py` & `invoke_subagent` (`subagent_alias` fallback):** Antigravity restricts `run_workflow` and `invoke_subagent` to `inherit | flash_lite | flash | pro`. `build_plan.py` resolves `antigravity_subagent_alias(...)` per role (`fast` -> `flash`, `workhorse` -> `pro`, `flagship` -> `inherit`, or explicit bare aliases) before overlaying `Args.models`; on `invoke_subagent`, omit `Model` or pass `"inherit"` so `agy_hooks.py` injects `subagent_alias` automatically (explicit `Model` values are preserved).

4. **Git Write Discipline & Worktrees:**
   - Main session runs read-only git commands only.
   - For worktree lifecycle (`worktree_create`, `worktree_list`, `worktree_remove`), call `paddock_paddock-worktree` MCP directly after Gate 2 approval (or delegate to shell-free `generalist`), with `paddock worktree <create|list|remove>` CLI as parity/fallback. `worktree_create` updates `.git/info/exclude` (never tracked `.gitignore`), copies matching untracked specs/plans preserving `mtime`, and initializes `.codegraph/` if present; `worktree_remove` triggers a `PreToolUse` `ask` prompt and enforces non-force removal (`git worktree remove` + optional `git branch -d` only, never `--force` or `-D`).
   - Dispatch commits, pushes, and PR workflows (`add`/`commit`/`push`/`gh pr`) via `paddock-implementer` with `Mode: git` and **intent only** (never dictate commit messages). Never push tags (`--tags`, `--follow-tags`) or force-delete without confirmation.

5. **Shared Memory (`paddock-mem` MCP) & Solo Escape:**
   - Pass explicit `cwd` on every `mem_select`, `mem_write`, `mem_validate`, `mem_stale`, `mem_deprecate`, and `mem_index` call (plugin MCP tools are namespaced under `paddock_paddock-mem`).
   - Setting `PADDOCK_SOLO=1` disables mechanical enforcement hooks for solo sessions.
