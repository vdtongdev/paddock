# Paddock Canonical Orchestration Loop Contract

Platform-agnostic contract implemented by every Paddock platform adapter.

---

## 1. Orchestrator Role & Model-Tier Fitting

- **Main session = pure orchestrator:** Clarifies ambiguity, writes specs, reviews plans, handles escalations, and delivers final summaries. Never burns flagship context on bulk code reading, web research, or direct source edits.
- **Model tiers:**
  - **Fast / lightweight:** Lookups (`Mode: lookup`), summaries, doc/text edits, mechanical edits, and delegated `paddock-worktree` MCP operations (`generalist`, which remains shell-free).
  - **Mid / workhorse:** Coding, tests, UI, planning, building/workflow execution, verification, git writes (`implementer`, `ui`, `planner`, `builder`, `verifier`).
  - **Flagship:** Main session orchestrator.

---

## 2. Bug Intake & Two-Lane Sort

1. **Bug intake (`Mode: diagnose` first):** Never fix bugs ad hoc. Dispatch `implementer` in `Mode: diagnose` first to reproduce with a failing regression test and identify root cause (`file:line`).
2. **Quick lane:**
   - Eligible only when: single dispatch, at most ~3 known files, zero open design decisions, no auth/secrets/migrations/public API changes, and a single verification command.
   - Two `FAIL: impl` verdicts or any `BLOCKED` status promotes the task to the **Plan lane**.
3. **Plan lane:**
   1. Orchestrator writes `docs/specs/<slug>.md` (including **Security** and optional **UI Design** sections).
   2. **User Approval Gate 1 (Hard Stop):** Present spec and wait for explicit user approval before planning.
   3. Dispatch `planner` to author `docs/plans/<slug>.md`. Every task must declare `Agent:`, `Files:`, `Scope:`, and `Acceptance:`.
   4. Orchestrator reviews the plan against the spec and presents it for **User Approval Gate 2 (Hard Stop)**.
   5. Create or reuse `.worktrees/<slug>` by calling `paddock-worktree` (`worktree_create`) directly from the main orchestrator (or delegating to fast-tier, shell-free `generalist`), then pass `worktree_path` into the build workflow (`Args.worktree`), parallel `swarm` workers, or `builder`.
   6. Execute the approved plan task by task inside the isolated worktree, verifying each task independently before advancing.
   7. Return one concise summary quoting observed verifier evidence.

---

## 3. Typed Verdicts, Routing & Retry Caps

Every task verification returns exactly one typed verdict:

| Verdict | Meaning & Routing | Retry Cap |
| :--- | :--- | :--- |
| `PASS` | Acceptance met with observed evidence -> advance to next task. | — |
| `FAIL: impl` | Implementation/test defect -> retry same worker with verifier findings. | **2** attempts -> escalate to `FAIL: plan` |
| `FAIL: plan` / `BLOCKED: plan` | Task decomposition or ordering gap -> `planner` revises affected task(s) only. | **2** revisions per task -> escalate to orchestrator & user |
| `FAIL: spec` / `BLOCKED: spec` | Missing/contradictory requirement -> stop build, escalate to orchestrator & user. | **0** (immediate stop) |
| `FAIL: env` / `BLOCKED: env` | Toolchain/network/sandbox/environment failure -> rerun flaky test once; if still failing, escalate to orchestrator. | Uncapped (flaky rerun once first) |
| `DISPUTE` | Worker contests verifier rejection with concrete evidence -> allowed once per task (counts as one worker attempt). Second dispute treated as `FAIL: impl`. | **1** per task |

---

## 4. Optional UI Verification (`impeccable`)

For UI tasks, load the `impeccable` skill if installed and run `npx impeccable detect --json <target>`:
- Exit `0`: pass.
- Exit `2`: `FAIL: impl` (accessibility/design defect).
- Exit `1`: `FAIL: env`.
- If `impeccable` is not installed, record the gap under `## Unverified` and relay once per session (soft dependency).

---

## 5. Git Write Discipline & Escape Hatch

- **Read-only git in main session:** Status, diff, log, branch, worktree inspection (`worktree_list` / `paddock worktree list`).
- **Worktree lifecycle (`paddock-worktree` MCP & `paddock worktree` CLI):**
  - `worktree_create` (called directly by main orchestrator after Gate 2 approval or via shell-free `generalist`) updates local `.git/info/exclude` (never tracked `.gitignore`), idempotently creates or reuses `.worktrees/<slug>` on `<prefix>/<slug>`, copies matching untracked specs/plans preserving `mtime` (enforcing Gate 2 when `plan_path` is passed), and runs non-fatal `codegraph init -y` if `.codegraph/` exists.
  - `worktree_remove` always triggers a `PreToolUse` `ask` confirmation prompt and enforces non-force cleanup (`git worktree remove` + optional `git branch -d` only; never `--force` or `-D`).
  - `paddock worktree <create|list|remove>` provides full CLI parity and fallback.
- **Git writes via `Mode: git`:** Dispatch worker with intent only (never dictate commit messages) for commits, PR creation, `gh pr merge --merge`, and post-merge `git pull --ff-only`. Never push tags (`--tags`, `--follow-tags`), never force-delete without user confirmation, and never skip branch/PR discipline for "small" changes.
- **Escape hatch:** Setting `PADDOCK_SOLO=1` disables enforcement hooks for solo sessions.
