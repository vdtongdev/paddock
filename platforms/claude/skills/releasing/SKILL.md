---
name: releasing
description: GitHub Flow, SemVer 2.0.0, Keep a Changelog 1.1.0, Conventional Commits 1.0.0, worktree lifecycle, and safe PR merge rules.
---

# Releasing, Branching & Worktree Discipline

## 1. Standards

- **Versioning:** Semantic Versioning 2.0.0 (`MAJOR.MINOR.PATCH`).
- **Changelog:** Keep a Changelog 1.1.0 (`Added`, `Changed`, `Deprecated`, `Removed`, `Fixed`, `Security` under `## [Unreleased]` and dated releases).
- **Commits:** Conventional Commits 1.0.0 (`feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `chore:`). Describe intent concisely; workers receive git intent, never pre-dictated commit strings.
- **Branching:** GitHub Flow — `main`/`master` stays deployable; all changes land via feature branch + Pull Request (no size exemptions).

## 2. Worktree-First Isolation

1. Add `.worktrees/` to local `.git/info/exclude` (never edit tracked `.gitignore`).
2. Create an isolated worktree per feature/task:
   ```bash
   git worktree add -b feat/<slug> .worktrees/<slug> main
   ```
3. Copy any required untracked specs/plans into the worktree if not yet committed.
4. **Per-worktree CodeGraph:** If the repository uses CodeGraph, run `codegraph init -y` inside `.worktrees/<slug>` rather than sharing stale index state across worktrees.

## 3. PR Merge & Cleanup Order

1. Verify tests and sync checks pass inside the worktree.
2. Push branch and create PR (`gh pr create`), wait for checks/review as required.
3. Merge via `gh pr merge --merge` (do not delete branches blindly mid-flight).
4. Clean up strictly in order:
   1. `git worktree remove .worktrees/<slug>`
   2. `git branch -d feat/<slug>`
   3. `git pull --ff-only` on `main`

## 4. Hard Git Safety Bans

- **Never push tags:** Never run `git push --tags` or `--follow-tags` (reject in verification).
- **Never force-delete:** Never run `git branch -D` or `git worktree remove --force` without explicit user confirmation.
- **Never write directly on `main`/`master`:** Only spec/plan docs (`docs/specs/`, `docs/plans/`) and `~/.paddock/` memory writes are allowed outside a feature worktree.
