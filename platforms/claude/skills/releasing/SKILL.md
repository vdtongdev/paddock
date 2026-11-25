---
name: releasing
description: "GitHub Flow, SemVer 2.0.0, Keep a Changelog 1.1.0, Conventional Commits 1.0.0, paddock-worktree MCP lifecycle, and safe PR merge rules."
---

# Releasing, Branching & Worktree Discipline

## 1. Standards

- **Versioning:** Semantic Versioning 2.0.0 (`MAJOR.MINOR.PATCH`).
- **Changelog:** Keep a Changelog 1.1.0 (`Added`, `Changed`, `Deprecated`, `Removed`, `Fixed`, `Security` under `## [Unreleased]` and dated releases).
- **Commits:** Conventional Commits 1.0.0 (`feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `chore:`). Describe intent concisely; workers receive git intent, never pre-dictated commit strings.
- **Branching:** GitHub Flow — `main`/`master` stays deployable; all changes land via feature branch + Pull Request (no size exemptions).

## 2. Worktree-First Isolation (`paddock-worktree` MCP & CLI)

Prefer the host-side `paddock-worktree` MCP tools (`worktree_create`, `worktree_list`, `worktree_remove`) or `paddock worktree` CLI parity so `.git/` remains protected from terminal sandboxes:

1. **Orchestrator or fast-tier `generalist`:** After plan approval (Gate 2), the main orchestrator calls `worktree_create(cwd, slug, prefix="feat", plan_path?)` directly — or delegates to shell-free `generalist` (`fast` tier, `mcp` capability) — and passes the returned `worktree_path` to the build workflow, parallel workers, or `builder`.
2. **Turnkey isolation:** `worktree_create` ensures `.worktrees/` exists in local `.git/info/exclude` (never tracked `.gitignore`), idempotently creates or reuses `.worktrees/<slug>` on `<prefix>/<slug>`, copies matching untracked `docs/specs/<slug>*.md`, `docs/plans/<slug>*.md`, and optional `plan_path`/`copy_paths` preserving `mtime`, and runs non-fatal `codegraph init -y` inside `.worktrees/<slug>` when `<repo_root>/.codegraph/` exists.
3. **CLI parity/fallback:**
   ```bash
   paddock worktree create <slug> [--prefix feat] [--base REF] [--plan-path PATH] [--copy PATH ...] [--no-codegraph] [--cwd DIR] [--json]
   paddock worktree list [--cwd DIR] [--json]
   ```
4. **Manual git fallback (outside Paddock tooling):**
   - Add `.worktrees/` to `.git/info/exclude` (never edit tracked `.gitignore`).
   - Run `git worktree add -b feat/<slug> .worktrees/<slug> main`, copy untracked specs/plans preserving timestamps, and run `codegraph init -y` inside `.worktrees/<slug>` if `.codegraph/` is used.

## 3. PR Merge & Cleanup Order

1. Verify tests and sync checks pass inside the worktree.
2. Push branch and create PR (`gh pr create`), wait for checks/review as required.
3. Merge via `gh pr merge --merge` (do not delete branches blindly mid-flight).
4. Clean up strictly in order:
   1. Remove the worktree (and optionally its merged branch via `git branch -d`) using `worktree_remove(cwd, slug, delete_branch=true)` via `paddock-worktree` MCP — which triggers a `PreToolUse` `ask` confirmation prompt and enforces strictly non-force removal — or CLI fallback `paddock worktree remove <slug> --delete-branch [--cwd DIR] [--json]`. Manual git equivalent:
      1. `git worktree remove .worktrees/<slug>`
      2. `git branch -d feat/<slug>`
   2. `git pull --ff-only` on `main`

## 4. Hard Git Safety Bans

- **Never push tags:** Never run `git push --tags` or `--follow-tags` (reject in verification).
- **Never force-delete:** Never run `git branch -D` or `git worktree remove --force` without explicit user confirmation.
- **Never write directly on `main`/`master`:** Only spec/plan docs (`docs/specs/`, `docs/plans/`) and `~/.paddock/` memory writes are allowed outside a feature worktree.
