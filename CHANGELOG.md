# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Unified Python 3.12+ stdlib typed-memory engine (`core/memory/memory.py`) with worktree-shared repo slugs, `PADDOCK_HOME` override, atomic writes, and read-only `import-claude`.
- Zero-dependency stdlib JSON-RPC 2.0 stdio MCP server (`core/memory/mcp_server.py`, server `paddock-mem`) exposing `mem_select`, `mem_write`, `mem_validate`, `mem_stale`, `mem_deprecate`, and `mem_index`.
- Canonical orchestration contract (`core/specs/loop-contract.md`) and platform-agnostic skills (`antipatterns`, `verifying`, `releasing`, `memory`).
- Sync & drift-check utilities (`scripts/sync.py`, `scripts/install.py`) with zero git symlinks.
- Phase 1 Antigravity adapter (`platforms/antigravity/`) featuring `agy_hooks.py`, deterministic `workflows/build_plan.py`, `paddock-*` custom agents, `rules/AGENTS.md`, and `mcp_config.json`.
- Phase 1 Claude Code adapter (`platforms/claude/`, `.claude-plugin/marketplace.json`) featuring `claude_hooks.py`, 6 frontmatter-pinned agents, dual POSIX/Windows hooks, and `.mcp.json`.
- End-to-end `pytest` suite covering memory core (including Foreman bug regression tests), stdio MCP framing, sync parity, Antigravity hooks/workflow state machine, and Claude hooks.

### Fixed
- Separate `refresh_layer()` `(problems, notices)` return so backing up non-generated `MEMORY.md` to `MEMORY.md.bak` is reported as a notice rather than a validation error.
- Exact resolved-path matching in `deprecate()` instead of substring filename matching.
- Clean 3,000-character problem truncation in `format_problems_capped()` without emitting a leading `; ...` prefix.
