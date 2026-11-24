---
name: memory
description: Unified typed-memory schema, fitness rules, verification rules, ~/.paddock/ layers, and paddock-mem MCP tool usage.
---

# Paddock Unified Memory (`~/.paddock/`)

All Paddock platforms share one private local memory store under `~/.paddock/` (`PADDOCK_HOME` overrides this root for tests and isolation):
- **User layer:** `~/.paddock/memory/` (`user`, `feedback`, global `reference` / traps) + generated `MEMORY.md`
- **Project layer:** `~/.paddock/projects/<repo-slug>/memory/` (`project`, project `reference` / traps) + generated `MEMORY.md`

`<repo-slug>` comes from `git rev-parse --path-format=absolute --git-common-dir`, so main checkouts and `.worktrees/*` share the same project memory.

## 1. Entry Schema (`<kebab-case-name>.md`)

```markdown
---
name: kebab-case-name
description: Single-line summary used for recall and MEMORY.md index
metadata:
  type: user | feedback | project | reference
  trap: true                      # optional; set true for costly gotchas
  paths: [src/**, tests/test_a.py]
  topics: [mcp, windows]
  status: active | deprecated
  superseded_by: newer-entry      # optional when deprecated
  verified: human | model
  verified_on: 2026-11-23
  stale_after: 2027-05-23 | never # required for project and reference
---
Body text. Link related entries with [[other-name]].
```

Trap entries (`trap: true`) **must** contain all three sections in the body:
- `**Symptom:**`
- `**Cause:**`
- `**Do instead:**`

## 2. Fitness & Verification Rules

- **Fitness test:** Store only durable cross-session facts (user preferences, architecture invariants, non-obvious traps, environment quirks). Never store transient task status, obvious code facts, or secrets.
- **Verification rule:** Use `verified: human` only when explicitly confirmed by the user; otherwise use `verified: model`.
- **Staleness:** Set realistic `stale_after` dates (`YYYY-MM-DD`, or `never` only for permanent invariants).

## 3. `paddock-mem` MCP Tools

Prefer the `paddock-mem` MCP server tools over manual file editing:
- `mem_select(cwd, paths?, topics?, type?, traps_only?)`: Recall matching active entries (project layer first, traps first).
- `mem_write(cwd, layer, name, description, metadata, body)`: Validates frontmatter before writing atomically inside `~/.paddock/`, refreshes `MEMORY.md`, and warns on dangling `[[links]]`.
- `mem_validate(cwd)`: Check both layers for schema or filename errors.
- `mem_stale(cwd, today?)`: List active entries past `stale_after`.
- `mem_deprecate(cwd, name, superseded_by?)`: Mark deprecated, rewrite `[[links]]` across active entries, and refresh indexes.
- `mem_index(cwd)`: Regenerate `MEMORY.md` indexes in both layers.

One-time read-only migration from legacy Claude memory:
`uv run --no-project --script core/memory/memory.py import-claude [--dry-run] [--cwd <repo>]`
