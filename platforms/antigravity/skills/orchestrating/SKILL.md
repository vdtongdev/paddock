---
name: orchestrating
description: "Antigravity Paddock orchestrator runbook for two-lane routing, approval gates, paddock-worktree MCP isolation, exact-model swarm dispatch, deterministic workflows/build_plan.py dispatch, and Mode: git writes."
---

# Paddock Orchestrating (Antigravity)

Follow `core/specs/loop-contract.md` using Antigravity's native primitives:

## 1. Model Dispatch: `swarm` (Exact Concrete Model) vs. `build_plan.py` / `invoke_subagent` (`subagent_alias`)

Model profiles live in `~/.paddock/models.json` and optional per-project `~/.paddock/projects/<slug>/models.json` (manage via `/paddock-models` or `paddock_core/models.py`).

- **Exact model + effort (`swarm`, `action="add"`):** Use Paddock worker names (`generalist`, `implementer`, `ui`, `planner`, `verifier`, `paddock-<role>`, or `<role>-<k>`) and **omit `model`** so `agy_hooks.py` enforces `plan-gate` / `Mode: git`, injects `VERIFY_RULES` + matched traps into `brief`, and injects the exact resolved `swarm_model` (`gemini-4-hydrogen-*`, `gemini-4-neon-*`, `gemini-4-carbon-*`, `claude-sonnet-5-5-*`, etc.). Pass explicit `model` only for a one-off override.
- **Deterministic workflow (`workflows/build_plan.py` via `run_workflow`):** Antigravity restricts `run_workflow` and `invoke_subagent` to the 4-value enum `inherit | flash_lite | flash | pro`. `build_plan.py` loads `models.py` and resolves `antigravity_subagent_alias(...)` per role (`flash` for `paddock-generalist`, `pro` for others under `balanced`), then overlays explicit `Args.models`.
- **Single subagent (`invoke_subagent`):** Omit `Model` or pass `"inherit"` so `agy_hooks.py` injects the resolved `subagent_alias` via `overwrite` (explicit `Model` values such as `"pro"` or `"flash_lite"` are preserved untouched).

## 2. Quick Lane vs. Plan Lane

- **Bug reports:** Always dispatch `implementer` (`swarm` or `invoke_subagent` `paddock-implementer`, `Mode: diagnose`) first to produce a failing regression test and `file:line` root cause.
- **Lookup (`Mode: lookup`):** Dispatch `generalist` (`swarm` or `invoke_subagent` `paddock-generalist`) for broad codebase discovery or web research (`search_web`).
- **Quick (`Mode: quick`):** Single dispatch (`<= 3` known files, no open decisions, no auth/secrets/migrations/public API, one check command).
- **Plan lane:**
  1. Call `mem_select` with repo `cwd` for relevant traps and conventions.
  2. Use `ask_question` to resolve ambiguity, then write `docs/specs/<slug>.md` (with **Security** and optional **UI Design**).
  3. Present spec with `RequestFeedback: true` and **STOP (User Approval Gate 1)**.
  4. Dispatch `planner` (`invoke_subagent` `paddock-planner` or `swarm` `planner`) pointing to the approved spec path to produce `docs/plans/<slug>.md`.
  5. Review the plan against the spec, present it to the user, and **STOP (User Approval Gate 2)**.
  6. Call `paddock_paddock-worktree` (`worktree_create`) directly (or delegate to fast-tier, shell-free `generalist`) to create/reuse `.worktrees/<slug>`, copy untracked specs/plans preserving `mtime`, and obtain `worktree_path`.
  7. Execute `platforms/antigravity/workflows/build_plan.py` via `run_workflow` with `Args={"plan_path": "docs/plans/<slug>.md", "worktree": "<worktree_path>", ...}` (optionally passing `Args.models` overrides) — or dispatch `swarm` workers referencing `worktree_path` and the approved plan for parallel exact-model tasks.
  8. Report one concise final summary quoting verifier evidence.

## 3. Worktree MCP Lifecycle & Git Writes (`Mode: git`)

- **Worktree MCP (`paddock_paddock-worktree`):** Main orchestrator calls `worktree_create`, `worktree_list`, and `worktree_remove` directly (or delegates to fast-tier `generalist`, which keeps `shell` disallowed).
  - `worktree_create` updates `.git/info/exclude` (never tracked `.gitignore`), idempotently creates or reuses `.worktrees/<slug>` on `<prefix>/<slug>`, copies matching untracked specs/plans preserving `mtime` (enforcing Gate 2 when `plan_path` is passed), and runs non-fatal `codegraph init -y` if `.codegraph/` exists.
  - `worktree_remove` always triggers a `PreToolUse` `ask` confirmation prompt and stays strictly non-force (`git worktree remove` + optional `git branch -d` only; never `--force` or `-D`).
- **CLI parity/fallback:** Use `paddock worktree <create|list|remove>` (`--json`) when running outside MCP.
- **Commits, PRs & merges (`Mode: git`):** Dispatch `implementer` (`Mode: git` via `swarm` or `invoke_subagent` `paddock-implementer`) for commits from intent only, PR creation via `gh pr create`, `gh pr merge --merge`, and post-merge `git pull --ff-only`.
- Never pass `--tags`, `--follow-tags`, `branch -D`, or `worktree remove --force` without explicit user approval.
