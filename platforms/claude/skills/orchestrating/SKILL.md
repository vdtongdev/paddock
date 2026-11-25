---
name: orchestrating
description: "Claude Code Paddock orchestrator runbook for two-lane routing, User Approval Gates 1 & 2, paddock-worktree MCP setup, builder dispatch, and Mode: git writes."
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
   - Call `paddock-worktree` (`mcp__plugin_paddock_paddock-worktree__worktree_create`) directly (or delegate to fast-tier, shell-free `generalist` with `mcp` capability) to create/reuse `.worktrees/<slug>`, then pass the returned `worktree_path` to `builder` referencing the approved plan path.
   - Present one concise summary quoting observed verifier evidence.
5. **Worktree MCP & Git Writes (`Mode: git`):**
   - **Worktree lifecycle (`paddock-worktree` MCP):** `worktree_create` updates `.git/info/exclude` (never tracked `.gitignore`), copies matching untracked specs/plans preserving `mtime` (Gate 2 enforced if `plan_path` is passed), and runs non-fatal `codegraph init -y` if `.codegraph/` exists; `worktree_list` inspects registered checkouts; `worktree_remove` triggers a `PreToolUse` `ask` confirmation prompt and deletes strictly non-force (`git worktree remove` + optional `git branch -d` only, never `--force` or `-D`).
   - **CLI parity/fallback:** `paddock worktree <create|list|remove>` (`--json`).
   - **Commits & PRs:** Dispatch `implementer` with `Mode: git` and intent only (never `--tags`/`--follow-tags` or force-deletes without explicit user approval).
