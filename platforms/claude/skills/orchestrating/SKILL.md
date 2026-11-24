---
name: orchestrating
description: Claude Code Paddock orchestrator runbook for two-lane routing, User Approval Gates 1 & 2, builder dispatch, and Mode: git writes.
---

# Paddock Orchestrating (Claude Code)

Follow `core/specs/loop-contract.md` using Claude Code's native primitives:

1. **Pure Orchestrator:** Main session clarifies, writes specs, reviews plans, handles escalations, and delivers final summaries (`guard` and `search-guard` enforce no direct edits or searches from main session).
2. **Bug Intake:** Always dispatch `implementer` with `Mode: diagnose` first (failing regression test + root cause `file:line`).
3. **Quick Lane:** Single `Agent` dispatch with `Mode: quick` (`<= 3` known files, no open decisions, no auth/secrets/migrations/public API, one check command). Two `FAIL: impl` or `BLOCKED` promotes to Plan lane.
4. **Plan Lane:**
   - Call `mem_select` via `paddock-mem` MCP for relevant traps and conventions.
   - Author `docs/specs/<slug>.md` (Security + optional UI Design) and **STOP for User Approval Gate 1**.
   - Dispatch `planner` referencing the approved spec to author `docs/plans/<slug>.md`.
   - Review plan vs. spec and **STOP for User Approval Gate 2**.
   - Dispatch `builder` referencing the approved plan path, then present the quoted verifier summary.
5. **Git Writes (`Mode: git`):** Dispatch `implementer` with `Mode: git` and intent only (`.worktrees/<slug>`, no `--tags`/`--follow-tags`, no force-deletes).
