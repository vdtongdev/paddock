# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-11-25

### Added
- Canonical platform-agnostic specialist definitions (`core/agents/*.md`) and stdlib compiler/applier (`core/config/agents.py`) compiling Antigravity (`paddock-*.md`) and Claude Code (`*.md`) profiles with capability-to-tool mapping and role footers.
- Versioned multi-provider model configuration engine (`core/config/models.py`, `schema_version: 1`) supporting global (`~/.paddock/models.json`) and worktree-shared per-project (`~/.paddock/projects/<slug>/models.json`) precedence, `budget`/`balanced`/`quality` presets, `google`/`anthropic`/`openai`/`native` tier catalogs, strict validation, atomic writes, and fail-open fallback.
- Cross-platform `/paddock-models` skill (`core/skills/paddock-models/SKILL.md`) and CLI (`scripts/models.py`, `paddock_core/models.py`) exposing `show`, `preset`, `set-provider`, `set-role`, `set-tier`, `set-override`, `apply`, and `reset`.
- Unit and integration tests in `tests/test_models_config.py` and `tests/test_agents_standard.py` covering multi-provider catalogs, 6-step precedence, validation, CLI subcommands, canonical agent compilation, and standalone `apply`.
- Unified Python 3.12+ stdlib typed-memory engine (`core/memory/memory.py`) with worktree-shared repo slugs, `PADDOCK_HOME` override, atomic writes, and read-only `import-claude`.
- Zero-dependency stdlib JSON-RPC 2.0 stdio MCP server (`core/memory/mcp_server.py`, server `paddock-mem`) exposing `mem_select`, `mem_write`, `mem_validate`, `mem_stale`, `mem_deprecate`, and `mem_index`.
- Canonical orchestration contract (`core/specs/loop-contract.md`) and platform-agnostic skills (`antipatterns`, `verifying`, `releasing`, `memory`).
- Sync & drift-check utilities (`scripts/sync.py`, `scripts/install.py`) with zero git symlinks.
- Phase 1 Antigravity adapter (`platforms/antigravity/`) featuring `agy_hooks.py`, deterministic `workflows/build_plan.py`, `paddock-*` custom agents, `rules/AGENTS.md`, and `mcp_config.json`.
- Phase 1 Claude Code adapter (`platforms/claude/`, `.claude-plugin/marketplace.json`) featuring `claude_hooks.py`, 6 frontmatter-pinned agents, dual POSIX/Windows hooks, and `.mcp.json`.
- End-to-end `pytest` suite covering memory core (including Foreman bug regression tests), stdio MCP framing, sync parity, Antigravity hooks/workflow state machine, and Claude hooks.

### Changed
- Updated `scripts/sync.py`, `scripts/install.py`, and `tests/test_sync_drift.py` to sync `core/config/{models,agents}.py`, `core/agents/*.md` (`paddock_core/agents/`), and `core/skills/paddock-models/`, compile tracked platform agent profiles from built-in `balanced` defaults, and prune stale synced files.
- Integrated dynamic model resolution into Antigravity (`platforms/antigravity/workflows/build_plan.py`, `hooks.json`, `scripts/agy_hooks.py`, `rules/AGENTS.md`, `skills/orchestrating/SKILL.md`) for `SessionStart` summaries, `invoke_subagent` `subagent_alias` injection, and `swarm` (`action="add"`) exact `swarm_model` + `plan-gate`/trap enforcement.
- Integrated model awareness into Claude Code (`platforms/claude/scripts/claude_hooks.py`) for `SessionStart` `model/effort` reporting and `PreToolUse` (`Agent`) non-blocking project-override `additionalContext` notices.
- Extended `tests/test_agy_hooks.py`, `tests/test_agy_workflow.py`, and `tests/test_claude_hooks.py` to cover Antigravity `swarm`/`invoke_subagent`/`build_plan.py` and Claude Code hook model resolution.

### Fixed
- Separate `refresh_layer()` `(problems, notices)` return so backing up non-generated `MEMORY.md` to `MEMORY.md.bak` is reported as a notice rather than a validation error.
- Exact resolved-path matching in `deprecate()` instead of substring filename matching.
- Clean 3,000-character problem truncation in `format_problems_capped()` without emitting a leading `; ...` prefix.
- In-place atomic Antigravity plugin reinstall in `scripts/install.py` with stale-entry pruning to prevent Windows `[WinError 32]` directory handle locks.

[Unreleased]: https://github.com/vdtongdev/paddock/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/vdtongdev/paddock/releases/tag/v0.2.0
