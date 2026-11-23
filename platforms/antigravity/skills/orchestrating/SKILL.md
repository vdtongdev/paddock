---
name: orchestrating
description: Antigravity Paddock orchestrator runbook for two-lane routing, approval gates, workflows/build_plan.py dispatch, and Mode: git writes.
---

# Paddock Orchestrating (Antigravity)

Follow `core/specs/loop-contract.md` using Antigravity's native primitives:

## 1. Quick Lane vs. Plan Lane

- **Bug reports:** Always dispatch `paddock-implementer` (`Model: "flash"`, `Mode: diagnose`) first to produce a failing regression test and `file:line` root cause.
- **Lookup (`Mode: lookup`):** Dispatch `paddock-generalist` (`Model: "flash_lite"`) for broad codebase discovery or web research (`search_web`).
- **Quick (`Mode: quick`):** Single dispatch (`<= 3` known files, no open decisions, no auth/secrets/migrations/public API, one check command).
- **Plan lane:**
  1. Call `mem_select` with repo `cwd` for relevant traps and conventions.
  2. Use `ask_question` to resolve ambiguity, then write `docs/specs/<slug>.md` (with **Security** and optional **UI Design**).
  3. Present spec with `RequestFeedback: true` and **STOP (User Approval Gate 1)**.
  4. Dispatch `paddock-planner` (`Model: "flash"`) via `invoke_subagent` pointing to the approved spec path to produce `docs/plans/<slug>.md`.
  5. Review the plan against the spec, present it to the user, and **STOP (User Approval Gate 2)**.
  6. Execute `platforms/antigravity/workflows/build_plan.py` via `run_workflow` with `Args={"plan_path": "docs/plans/<slug>.md", ...}`.
  7. Report one concise final summary quoting verifier evidence.

## 2. Worktree & Git Writes (`Mode: git`)

- Dispatch `paddock-implementer` (`Mode: git`, `Model: "flash"`) for all git writes (worktree setup `.worktrees/<slug>` with `.git/info/exclude`, commits from intent only, PR creation via `gh pr create`, `gh pr merge --merge`, and post-merge cleanup).
- Never pass `--tags`, `--follow-tags`, `branch -D`, or `worktree remove --force` without explicit user approval.
