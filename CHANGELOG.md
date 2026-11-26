# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.2] - 2026-11-26

### Fixed
- Added a pre-flight Windows `uv tool` self-invocation guard (`_is_windows_uv_tool_self_invocation`, `_is_paddock_uv_receipt`, `_locked_update_payload`) in `paddock/updater.py` (`_run_uv_upgrade`) so `paddock update` detects running from a `uv tool`-installed `paddock.exe` environment on Windows and refuses to overwrite itself via `uv tool install --force`—preventing partial venv teardown (`ModuleNotFoundError: No module named 'paddock'`), leaving installed platform plugins and `~/.paddock/` untouched, and printing the two recovery commands (`uv tool install --force git+https://github.com/vdtongdev/paddock.git@<ref>` and `paddock install`) while preserving `--check`, `--dry-run`, `--plugins-only`, and missing-`uv` error behavior; updated `README.md` and added regression coverage in `tests/test_paddock_cli.py`.

## [0.3.1] - 2026-11-25

### Fixed
- Resolved the post-Gate-2 worktree deadlock in which `branch-guard` (`PreToolUse`) blocked direct source edits on `main`/`master` while terminal sandboxes blocked writes to `.git/` (`.git/info/exclude`, `git worktree add`), by routing worktree lifecycle operations through a host-side MCP server (`paddock-worktree`) and supporting `Args.worktree` relative plan resolution in `platforms/antigravity/scripts/agy_hooks.py` (`_resolve_relative_candidate`, `_resolve_dir_arg`, `_handle_run_workflow_gate`) so plans copied into `.worktrees/<slug>` resolve against the worktree before falling back to `cwd` or `repo_root`.

### Added
- Zero-dependency Python 3.12+ stdlib worktree lifecycle engine (`core/git/__init__.py`, `core/git/worktree.py`) and JSON-RPC 2.0 stdio MCP server (`core/git/worktree_mcp_server.py`, server `paddock-worktree`) exposing `worktree_create`, `worktree_list`, and `worktree_remove` to manage `.git/info/exclude`, idempotently create or reuse `.worktrees/<slug>` on `<prefix>/<slug>`, copy matching untracked specs/plans preserving `mtime`, and run non-fatal `codegraph init -y` when `.codegraph/` is present.
- `paddock worktree <create|list|remove>` CLI subcommands (`--prefix`, `--base`, `--plan-path`, `--copy`, `--no-codegraph`, `--delete-branch`, `--cwd`, `--json`) in `paddock/cli.py` for terminal parity and fallback outside MCP.
- `paddock-worktree` MCP registration in `platforms/antigravity/mcp_config.json` and `platforms/claude/.mcp.json`, plus `PreToolUse` MCP hooks in `platforms/antigravity/hooks.json`, `platforms/antigravity/scripts/agy_hooks.py`, `platforms/claude/hooks/hooks.json`, and `platforms/claude/scripts/claude_hooks.py` enforcing Approval Gate 2 when `plan_path` is passed to `worktree_create` and requiring an `ask` confirmation prompt on `worktree_remove`.
- Unit, stdio MCP framing, hook, CLI, and sync tests (`tests/test_worktree_core.py`, `tests/test_worktree_mcp.py`, and extensions to `tests/test_agy_hooks.py`, `tests/test_claude_hooks.py`, `tests/test_paddock_cli.py`, `tests/test_agents_standard.py`, `tests/test_sync_drift.py`).

### Changed
- Added `"mcp"` capability support to `core/config/agents.py` (`VALID_CAPABILITIES`, `CAPABILITY_TOOL_MAP`) and granted `mcp` to `core/agents/generalist.md` (keeping `shell` and `delegate` disallowed) so fast-tier `generalist` agents can run delegated `paddock-worktree` operations on Antigravity (`call_mcp_tool`) and Claude Code (`mcp__plugin_paddock_paddock-worktree__worktree_*`).
- Updated `scripts/sync.py` (`CORE_GIT_FILES`, `_VERSION_PARITY_SOURCES`) and `paddock/installer.py` (`_REQUIRED_ANTIGRAVITY_ASSETS`, `_REQUIRED_BUNDLE_ASSETS`) to sync, bundle, and verify version parity across `worktree.py` and `worktree_mcp_server.py` (10 total version parity sources).
- Updated `core/specs/loop-contract.md`, `core/skills/releasing/SKILL.md`, `platforms/antigravity/rules/AGENTS.md`, `platforms/*/skills/orchestrating/SKILL.md`, and `README.md` with `paddock-worktree` MCP and `paddock worktree` CLI documentation.

### Security
- Enforced strict slug (`SLUG_RE`), branch prefix (`VALID_PREFIXES`), and git ref (`BASE_REF_RE`) validation, symlink/Windows junction rejection (`_is_symlink_or_junction`, `_check_non_symlink_descendants`), `.worktrees/` containment checks, `copy_paths` traversal/escape/`.git/` bans, and `shell=False` subprocess execution in `core/git/worktree.py`.
- Restricted `worktree_remove` and `paddock worktree remove` to Paddock-managed `.worktrees/<slug>` checkouts using strictly non-force `git worktree remove` and optional non-force `git branch -d` (refusing main/external checkouts, `--force`, `-D`, and protected branches `main`/`master`).

## [0.3.0] - 2026-11-25

### Added
- Zero-dependency Python `3.12+` package configuration (`pyproject.toml`, `core/__init__.py`, `core/memory/__init__.py`) using `hatchling` to bundle `core/`, `platforms/antigravity/`, `platforms/claude/`, and `.claude-plugin/marketplace.json` under `paddock/_bundle/` for clone-free `uv tool install` and `uvx` distribution.
- Unified `paddock` CLI package (`paddock/__init__.py`, `paddock/version.py`, `paddock/installer.py`, `paddock/updater.py`, `paddock/cli.py`) providing `paddock install`, `paddock uninstall`, `paddock update`, `paddock status`, `paddock models ...`, and `paddock --version`.
- Source-root resolution (`resolve_source_root`, `source_mode`, `verify_bundle_integrity`), auto-detection for Antigravity (`~/.gemini`) and Claude Code (`claude` / `~/.claude`), automated `claude plugin marketplace` and `claude plugin install` execution with dry-run/missing-CLI fallback, and `collect_status()` inspection.
- GitHub Release updater (`paddock/updater.py`) syncing the `paddock` tool via `uv tool install --force` and updating installed platform plugins against the latest release tag (with `gh api` fallback, `--check`, `--version`, `--main`, `--platform`, `--plugins-only`, `--dry-run`, and Windows file-in-use recovery commands).
- Single-source SemVer validation (`check_version_parity`) in `scripts/sync.py` checking identical versions across `pyproject.toml`, `paddock/__init__.py`, `platforms/antigravity/plugin.json`, `platforms/claude/.claude-plugin/plugin.json`, and `mcp_server.py` (canonical and synced copies).
- GitHub Actions CI (`.github/workflows/ci.yml`) on `ubuntu-latest` and `windows-latest` running sync checks, `pytest`, wheel cleanliness checks, and out-of-repo isolated venv smoke tests, plus release automation (`.github/workflows/release.yml`) on `v*` tags producing deterministic plugin archives, wheels, `SHA256SUMS.txt`, and GitHub Releases.
- CLI, bundle fallback, updater, version parity, and workflow structural tests (`tests/test_paddock_cli.py`, `tests/test_sync_drift.py`).

### Changed
- Converted `scripts/install.py` and `scripts/models.py` into thin backward-compatible wrappers delegating to `paddock.installer` and `core.config.models.main`, and integrated `check_version_parity` into `scripts/sync.py`.
- Rewrote `README.md` with a user-first `Install` / `Update` / `Uninstall` / `paddock status` onboarding flow, `paddock models` CLI documentation, and contributor-only `Development` section.

### Security
- Hardcoded HTTPS GitHub repository/API URLs (`https://github.com/vdtongdev/paddock`) in `paddock/updater.py` with strict `^v\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$` tag validation, `shell=False` subprocess execution, and `GITHUB_TOKEN`/`GH_TOKEN` secret redaction.
- Enforced path-traversal and symlink/junction validation across source-root resolution and in-place plugin synchronization while guaranteeing `paddock uninstall` never deletes `~/.paddock/`.
- Scoped GitHub Actions workflow permissions to least privilege (`contents: read` in `ci.yml`; `contents: write` restricted to `v*` tag builds in `release.yml`).

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

[Unreleased]: https://github.com/vdtongdev/paddock/compare/v0.3.2...HEAD
[0.3.2]: https://github.com/vdtongdev/paddock/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/vdtongdev/paddock/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/vdtongdev/paddock/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/vdtongdev/paddock/releases/tag/v0.2.0
