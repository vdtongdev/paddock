# Implementation Plan: Paddock Worktree MCP Server, Cheap-Model Routing & CLI

Date: 2026-11-25
Spec: [docs/specs/worktree-mcp.md](../specs/worktree-mcp.md)
Target Branch: `feat/worktree-mcp` (worktree `.worktrees/worktree-mcp` off `main`)

---

## Execution Rules & Hard Constraints

- **Worktree isolation before source edits:** This repository is currently on `main`. Execute **Task 0** (`Mode: git`) to create `.worktrees/worktree-mcp` (with `.worktrees/` in `.git/info/exclude`, never tracked `.gitignore`), copy untracked `docs/specs/worktree-mcp.md` and `docs/plans/worktree-mcp.md` via `shutil.copy2`, and run `build_plan.py` with `Args.worktree` pointing to the worktree so `branch-guard` never blocks source/test/config edits.
- **Zero third-party runtime dependencies:** All code in `core/` and `paddock/` uses Python 3.12+ standard library only (`# /// script` / `# requires-python = ">=3.12"`).
- **Least-privilege git & shell boundaries:** Never pass `shell=True`, `--force`, `-D`, `--tags`, or `--follow-tags`; never edit `.gitignore`, `.git/hooks/`, or `.git/config`; keep `generalist` shell-free (`disallowed: [shell, delegate]`).
- **Real test file mapping:** `tests/test_cli_installer.py` referenced in Spec Section 7 does not exist in `tests/`; all CLI and installer tests live in `tests/test_paddock_cli.py`, which also asserts the exact set of `paddock` subcommands and `verify_bundle_integrity` assets.
- **Windows sandbox note:** Inside the Windows sandbox, `python -m pytest -q` and `python scripts/sync.py --check` run directly without external cache access; commands invoking `uv` (`uv run --with pytest pytest -q`, `uv build --wheel`) require `BypassSandbox: true` (or a writable temp `UV_CACHE_DIR`) when accessing the `uv` cache. Always output wheels to a temporary directory outside the repo (or clean up `dist/`) so `git status` stays clean.

---

### Task 0 — Set Up Isolated Feature Worktree & Verify Baseline

- **Agent:** `paddock-implementer`
- **Files:** `.git/info/exclude`, `.worktrees/worktree-mcp`, `docs/specs/worktree-mcp.md`, `docs/plans/worktree-mcp.md`
- **Scope:**
  - Execute in `Mode: git` from repository root `d:\Projects\paddock`:
    1. Ensure `.worktrees/` is present in `.git/info/exclude` (creating `.git/info/` if needed; never modify tracked `.gitignore`).
    2. Idempotently create or verify isolated feature worktree `.worktrees/worktree-mcp` on branch `feat/worktree-mcp` from `main`: `git worktree add -b feat/worktree-mcp .worktrees/worktree-mcp main` (or reuse if already registered on `feat/worktree-mcp`).
    3. Copy untracked `docs/specs/worktree-mcp.md` and `docs/plans/worktree-mcp.md` from main checkout into `.worktrees/worktree-mcp/docs/` via `shutil.copy2` (preserving `mtime` so Gate 2 approval checks remain valid).
    4. Because `.codegraph/` exists in the repository root, run non-fatal `codegraph init -y` inside `.worktrees/worktree-mcp` if `.worktrees/worktree-mcp/.codegraph/` is not yet present.
    5. Verify baseline sync and test suite inside `.worktrees/worktree-mcp`.
- **Acceptance:**
  - `git worktree list` shows `.worktrees/worktree-mcp` checked out on `[feat/worktree-mcp]`.
  - `git -C .worktrees/worktree-mcp diff --quiet main` exits `0` and `.gitignore` is untouched.
  - `python scripts/sync.py --check` inside `.worktrees/worktree-mcp` prints `OK: zero sync drift` (`exit 0`).
  - `python -m pytest -q` inside `.worktrees/worktree-mcp` passes all existing tests (`exit 0`).

### Task 1 — Implement Core Worktree Module (`core/git/worktree.py`) & Unit Tests

- **Agent:** `paddock-implementer`
- **Files:** `core/git/__init__.py`, `core/git/worktree.py`, `tests/test_worktree_core.py`
- **Scope:**
  - Create `core/git/__init__.py` as a minimal package marker (`"""Paddock core git and worktree utilities."""`) so `core.git.worktree` imports cleanly in both repo checkouts and wheel builds.
  - Implement pure Python 3.12+ stdlib `core/git/worktree.py` (`# /// script` / `# requires-python = ">=3.12"`), runnable both as `core.git.worktree` and standalone `platforms/<plat>/scripts/paddock_core/worktree.py`:
    - `VALID_PREFIXES = ("feat", "fix", "docs", "refactor", "chore", "test")`.
    - `SLUG_RE` validating `^[a-z0-9][a-z0-9._-]{0,63}$` strictly via `\Z` (rejecting trailing `\n`, `..` anywhere in slug, `/`, `\`, leading `-`, empty strings, non-ASCII, and control characters).
    - Safe git base ref validation rejecting leading `-`, empty strings, control chars, whitespace, `..`, `@{`, glob/revision operators (`~`, `^`, `:`, `?`, `*`, `[`, `\`), `.lock` suffix, and leading/trailing `/`.
    - `resolve_repo_root(cwd)` and internal `_resolve_git_paths(cwd)` using `git -C <cwd> rev-parse --path-format=absolute --git-common-dir` (failing cleanly on non-existent paths, non-git directories, or bare repositories).
    - Symlink and Windows directory junction guards (`is_symlink()` and `is_junction()`, matching `core/config/agents.py`) applied across every path component from `repo_root` down to `.worktrees/<slug>`, `<git_common_dir>/info/exclude`, and any `copy_paths` / `plan_path` inputs.
    - `copy_paths` / `plan_path` containment validation: `copy_paths` accepts relative paths only and rejects `..`, absolute/drive/UNC paths, symlinks/junctions, paths escaping `repo_root`, and any path inside `.git/`; `plan_path` allows relative paths or paths strictly inside `repo_root` (normalized to repo-relative, rejecting escapes, symlinks/junctions, and `.git/`).
    - `ensure_worktree_exclude(repo_root) -> bool`: atomically append `.worktrees/` to `<git_common_dir>/info/exclude` (creating `info/` if absent, preserving existing content and line endings; skipping if `.worktrees/` or `.worktrees` line already exists; returning `True` if updated else `False`). Never edit `.gitignore`, `.git/hooks/`, or `.git/config`.
    - `create_worktree(cwd, slug, *, prefix="feat", base=None, plan_path=None, copy_paths=None, init_codegraph=True) -> dict`:
      - Auto-detect base ref (`main` if `refs/heads/main` exists, else `master` if `refs/heads/master` exists, else `HEAD`).
      - Idempotently reuse `.worktrees/<slug>` if already registered on `<prefix>/<slug>` (`created=False`, `reused=True`, `reused_branch=False`), fail cleanly if `.worktrees/<slug>` is on a different branch/detached or is an untracked/non-worktree directory, attach existing local branch `<prefix>/<slug>` if not checked out elsewhere (`created=True`, `reused=False`, `reused_branch=True`) or fail naming the owning worktree if checked out elsewhere, else `git worktree add -b <prefix>/<slug> -- <path> <base>`.
      - Auto-copy untracked `docs/specs/<slug>*.md`, `docs/plans/<slug>*.md`, plus explicit `copy_paths` and `plan_path` (if untracked or missing in worktree) via `shutil.copy2` (preserving `mtime`), skipping identical existing files and never overwriting newer or tracked worktree edits.
      - Optional non-fatal `codegraph init -y` inside `worktree_path` (30s timeout) when `init_codegraph=True`, `(repo_root / ".codegraph").is_dir()`, `shutil.which("codegraph")` is available, and `(worktree_path / ".codegraph")` does not exist; return exact `codegraph_status` enum (`initialized`, `already_initialized`, `skipped_no_source_index`, `skipped_disabled`, `skipped_missing_cli`, `failed: <reason>`).
      - Return JSON-serializable dict matching Spec Section 2.2 (`repo_root`, `worktree_path`, `branch`, `base`, `created`, `reused`, `reused_branch`, `exclude_updated`, `copied_files`, `codegraph_status`).
    - `list_worktrees(cwd) -> dict`: parse `git worktree list --porcelain` returning `repo_root` and `worktrees` entries with `path`, `head`, `branch`, `bare`, `detached`, `locked`, `prunable`, `is_main`, `is_paddock_managed`, and `slug`.
    - `remove_worktree(cwd, slug, *, delete_branch=False) -> dict`: verify `<repo_root>/.worktrees/<slug>` is a registered Paddock-managed worktree (never main checkout or external worktrees), run strictly non-force `git worktree remove -- <path>` (fail cleanly if dirty or containing untracked files), and if `delete_branch=True` and branch is not `main`/`master`, run strictly non-force `git branch -d -- <branch>` (never `-D`; if unmerged refusal occurs, return `removed=True`, `branch_deleted=False`, `branch_delete_warning=<reason>` without raising).
    - Use `subprocess.run(..., shell=False)` with `--` separators on all git/codegraph commands.
  - Create `tests/test_worktree_core.py` covering slug/prefix/base-ref validation, symlink/junction & `copy_paths` traversal/`.git` rejection, `.git/info/exclude` idempotency and line-ending preservation, create/reuse/branch-attach/conflict handling, `shutil.copy2` `mtime` preservation and skip-newer/tracked guards, all `codegraph_status` outcomes (mocked CLI), `list_worktrees`, and non-force `remove_worktree` (dirty refusal, external/main refusal, merged `-d`, unmerged `-d` warning).
- **Acceptance:**
  - `python -m pytest -q tests/test_worktree_core.py` exits `0` with all tests passing and zero skipped assertions.

### Task 2 — Implement Stdio MCP Server (`core/git/worktree_mcp_server.py`) & MCP Tests

- **Agent:** `paddock-implementer`
- **Files:** `core/git/worktree_mcp_server.py`, `tests/test_worktree_mcp.py`
- **Scope:**
  - Implement `core/git/worktree_mcp_server.py` mirroring `core/memory/mcp_server.py`:
    - `# /// script` / `# requires-python = ">=3.12"` header, zero third-party deps, importing sibling `worktree` via `_THIS_DIR` on `sys.path` so both `core/git/` and flat `platforms/<plat>/scripts/paddock_core/` work identically.
    - `SUPPORTED_PROTOCOL_VERSIONS = {"2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"}`, `DEFAULT_PROTOCOL_VERSION = "2025-03-26"`, `SERVER_INFO = {"name": "paddock-worktree", "version": "0.3.0"}`.
    - Strict UTF-8 LF binary stdio framing via `sys.stdout.buffer` (zero `\r`), stderr-only `[paddock-worktree]` logging, silent handling of notifications (`"id" not in msg`), JSON-RPC `-32601` for `server/discover` and unknown methods, `-32700` parse errors, `-32600` invalid requests, `-32602` invalid `tools/call` params, and `isError: True` structured JSON payloads on tool failures.
    - Expose strictly 3 tools (`worktree_create`, `worktree_list`, `worktree_remove`), each requiring explicit `cwd`:
      - `worktree_create`: `cwd` (req), `slug` (req), optional `prefix`, `base`, `plan_path`, `copy_paths` (list of strings), `init_codegraph` (bool, default `true`).
      - `worktree_list`: `cwd` (req).
      - `worktree_remove`: `cwd` (req), `slug` (req), optional `delete_branch` (bool, default `false`).
    - Enforce strict runtime type validation in `dispatch_tool` (rejecting non-string/blank required fields, non-list/non-str `copy_paths`, and non-bool values for `init_codegraph` / `delete_branch`).
  - Create `tests/test_worktree_mcp.py` (mirroring `tests/test_mcp_server.py`) launching `core/git/worktree_mcp_server.py` as a subprocess from an unrelated `proc_cwd`:
    - Verify `server/discover` (`-32601`), `initialize` (`2025-11-25`, `serverInfo.name == "paddock-worktree"`), `notifications/initialized`, `ping`, `tools/list` (exact 3 tool names + schemas), `tools/call` for `worktree_create` / `worktree_list` / `worktree_remove` (including `delete_branch` and `plan_path` copy), error handling (`isError: True` on invalid slug, traversal, dirty removal, invalid bool/list types), strict LF-only stdout frames, and `SERVER_INFO["version"] == paddock.__version__`.
- **Acceptance:**
  - `python -m pytest -q tests/test_worktree_mcp.py tests/test_worktree_core.py` exits `0` with all tests passing and zero skipped assertions.

### Task 3 — Add `mcp` Capability to `core/config/agents.py`, `core/agents/generalist.md`, and Agent Standard Tests

- **Agent:** `paddock-implementer`
- **Files:** `core/config/agents.py`, `core/agents/generalist.md`, `tests/test_agents_standard.py`
- **Scope:**
  - Before editing, verify the exact Claude Code plugin MCP tool naming format for subagent `tools:` frontmatter against official Claude Code documentation via `search_web` (confirming `mcp__plugin_<plugin-name>_<server-name>__<tool-name>` — i.e. `mcp__plugin_paddock_paddock-worktree__worktree_create`, `mcp__plugin_paddock_paddock-worktree__worktree_list`, `mcp__plugin_paddock_paddock-worktree__worktree_remove` for plugin `paddock` in `platforms/claude/.claude-plugin/plugin.json` and server `paddock-worktree` — or exact documented format), and record the `URL + quote` in evidence.
  - Update `core/config/agents.py`:
    - Add `"mcp"` to `VALID_CAPABILITIES`.
    - Add `"mcp"` to `CAPABILITY_TOOL_MAP`: `"antigravity": ("call_mcp_tool",)` and `"claude": (<verified Claude Code plugin MCP worktree tool names>)`.
  - Update `core/agents/generalist.md`:
    - Set `capabilities: [read, edit, write, search, web, mcp]` while keeping `disallowed: [shell, delegate]` and `default_tier: fast`.
  - Update `tests/test_agents_standard.py`:
    - Update `loaded["generalist"].capabilities` assertion to expect `"mcp"`.
    - Include `"mcp"` in `all_caps` and `map_capabilities` assertions for both `antigravity` and `claude`.
    - Verify compiled Antigravity `paddock-generalist.md` includes `call_mcp_tool` and excludes `run_command`, and compiled Claude `generalist.md` includes the verified `mcp__...` worktree tools while preserving `disallowedTools: Bash, Agent`.
- **Acceptance:**
  - `python -m pytest -q tests/test_agents_standard.py` exits `0` with all tests passing and zero skipped assertions.
  - Worker and verifier evidence cite the verified Claude Code plugin MCP tool naming `URL + quote`.

### Task 4 — Register Platform MCP Configs, Update `scripts/sync.py` & `paddock/installer.py`, Run Sync

- **Agent:** `paddock-implementer`
- **Files:** `platforms/antigravity/mcp_config.json`, `platforms/claude/.mcp.json`, `scripts/sync.py`, `paddock/installer.py`, `tests/test_sync_drift.py`, `platforms/antigravity/scripts/paddock_core/worktree.py`, `platforms/antigravity/scripts/paddock_core/worktree_mcp_server.py`, `platforms/antigravity/scripts/paddock_core/agents.py`, `platforms/antigravity/scripts/paddock_core/agents/generalist.md`, `platforms/antigravity/agents/paddock-generalist.md`, `platforms/claude/scripts/paddock_core/worktree.py`, `platforms/claude/scripts/paddock_core/worktree_mcp_server.py`, `platforms/claude/scripts/paddock_core/agents.py`, `platforms/claude/scripts/paddock_core/agents/generalist.md`, `platforms/claude/agents/generalist.md`
- **Scope:**
  - Register `"paddock-worktree"` alongside `"paddock-mem"` in `platforms/antigravity/mcp_config.json` (`uv run --no-project --script scripts/paddock_core/worktree_mcp_server.py`, `env: {"PYTHONUTF8": "1"}`) and `platforms/claude/.mcp.json` (`uv run --no-project --script ${CLAUDE_PLUGIN_ROOT}/scripts/paddock_core/worktree_mcp_server.py`, `env: {"PYTHONUTF8": "1"}`).
  - Update `scripts/sync.py`:
    - Add `CORE_GIT_FILES = ("worktree.py", "worktree_mcp_server.py")`.
    - Sync `core/git/*` into `platforms/antigravity/scripts/paddock_core/` and `platforms/claude/scripts/paddock_core/` inside `sync_all`.
    - Extend `_VERSION_PARITY_SOURCES` for `check_version_parity` to include `core/git/worktree_mcp_server.py`, `platforms/antigravity/scripts/paddock_core/worktree_mcp_server.py`, and `platforms/claude/scripts/paddock_core/worktree_mcp_server.py` (10 total sources).
  - Update `paddock/installer.py`:
    - Add `platforms/antigravity/scripts/paddock_core/worktree.py` and `platforms/antigravity/scripts/paddock_core/worktree_mcp_server.py` to `_REQUIRED_ANTIGRAVITY_ASSETS`.
    - Add `platforms/claude/scripts/paddock_core/worktree.py` and `platforms/claude/scripts/paddock_core/worktree_mcp_server.py` to `_REQUIRED_BUNDLE_ASSETS`.
  - Run `python scripts/sync.py` to sync `core/git/*`, updated `core/config/agents.py`, `core/agents/generalist.md`, and compiled `generalist` profiles into `platforms/antigravity/` and `platforms/claude/`.
  - Update `tests/test_sync_drift.py`:
    - Assert `worktree.py` and `worktree_mcp_server.py` exist under `platforms/{antigravity,claude}/scripts/paddock_core/` in `test_repo_platforms_have_zero_sync_drift`.
    - Add drift detection and repair coverage for `CORE_GIT_FILES` and verify both platform MCP config files register `paddock-worktree`.
    - Expand `test_check_version_parity_catches_mismatch` to cover all 10 version parity locations (including `core/git/worktree_mcp_server.py` and its 2 synced copies).
- **Acceptance:**
  - `python scripts/sync.py --check` prints `OK: zero sync drift` and exits `0`.
  - `python -m pytest -q tests/test_sync_drift.py` exits `0` with all tests passing.

### Task 5 — Implement Antigravity & Claude Code `PreToolUse` Worktree Hooks + Tests

- **Agent:** `paddock-implementer`
- **Files:** `platforms/antigravity/hooks.json`, `platforms/antigravity/scripts/agy_hooks.py`, `platforms/claude/hooks/hooks.json`, `platforms/claude/scripts/claude_hooks.py`, `tests/test_agy_hooks.py`, `tests/test_claude_hooks.py`
- **Scope:**
  - **Antigravity (`platforms/antigravity/hooks.json`, `platforms/antigravity/scripts/agy_hooks.py`):**
    - Keep `_handle_branch_guard` unchanged.
    - Add `PreToolUse` matcher `"call_mcp_tool"` in `platforms/antigravity/hooks.json`.
    - In `agy_hooks.py`, intercept `call_mcp_tool` when `ServerName` (or `server_name`) is `"paddock_paddock-worktree"` or `"paddock-worktree"`:
      - `worktree_list`: `{"decision": "allow"}`.
      - `worktree_create`: if `Arguments.plan_path` is a non-empty string, resolve relative `plan_path` against `Arguments.cwd` / `cwd` / `repo_root` and validate via `_validate_plan_file` (Approval Gate 2), returning `{"decision": "deny", "reason": err}` on error; otherwise `{"decision": "allow"}`.
      - `worktree_remove`: return `{"decision": "ask", "reason": f"Confirm removing worktree '.worktrees/{slug}' (delete_branch={delete_branch}). Non-force only."}`.
    - Update `_handle_run_workflow_gate` in `agy_hooks.py` so when `Args.worktree` is supplied (relative or absolute) and `Args.plan_path` is relative (or resolved via `_resolve_doc_ref`), `plan_file` resolves cleanly across `(worktree_dir / plan_path)`, `(cwd / plan_path)`, and `(repo_root / plan_path)` (checking `worktree_dir` first when present so Gate 2 checks the active worktree copy, and falling back to parent repo `cwd` / `repo_root`).
  - **Claude Code (`platforms/claude/hooks/hooks.json`, `platforms/claude/scripts/claude_hooks.py`):**
    - Keep `guard` and `branch-guard` unchanged.
    - Add `PreToolUse` matcher `"mcp__.*paddock-worktree__.*"` in `platforms/claude/hooks/hooks.json` with `command`, `commandWindows`, and `timeout: 15`.
    - Add `_ask(reason)` helper in `claude_hooks.py` returning `hookSpecificOutput` with `hookEventName: "PreToolUse"`, `permissionDecision: "ask"`, and `permissionDecisionReason: reason`.
    - Extract shared `_validate_plan_gate` helper and intercept `mcp__.*paddock-worktree__(worktree_create|worktree_list|worktree_remove)` in `handle_pre_tool_use`:
      - `worktree_list`: allow (`{}`).
      - `worktree_create`: if `tool_input.plan_path` is a non-empty string, resolve relative path against `tool_input.cwd` / `cwd` / `repo_root`, validate plan file + Approval Gate 2 via `_check_approval_gate`, and return `_deny(...)` on violation; otherwise allow (`{}`).
      - `worktree_remove`: return `_ask(...)` displaying target `.worktrees/<slug>` and `delete_branch=<bool>`.
  - **Tests (`tests/test_agy_hooks.py`, `tests/test_claude_hooks.py`):**
    - Test Antigravity `call_mcp_tool` routing (`paddock_paddock-worktree` and `paddock-worktree`: `worktree_list` allow, `worktree_create` allow without `plan_path`, Gate 2 `deny` on unapproved/missing/malformed `plan_path`, allow on approved `plan_path`, `ask` on `worktree_remove`, allow for unrelated MCP servers) plus `Args.worktree` + relative `Args.plan_path` resolution in `run_workflow`, and assert `call_mcp_tool` matcher in `platforms/antigravity/hooks.json`.
    - Test Claude Code `mcp__plugin_paddock_paddock-worktree__worktree_*` routing (`allow`, Gate 2 `deny`, `ask` on `worktree_remove`), and assert `mcp__.*paddock-worktree__.*` matcher in `platforms/claude/hooks/hooks.json`.
- **Acceptance:**
  - `python -m pytest -q tests/test_agy_hooks.py tests/test_agy_workflow.py tests/test_claude_hooks.py` exits `0` with all tests passing and zero skipped assertions.

### Task 6 — Add `paddock worktree <create|list|remove>` CLI Subcommands & CLI/Bundle Tests

- **Agent:** `paddock-implementer`
- **Files:** `paddock/cli.py`, `tests/test_paddock_cli.py`
- **Scope:**
  - Update `paddock/cli.py`:
    - Import `worktree` from `core.git`.
    - Add `worktree` subcommand group with `create`, `list`, and `remove` matching Spec Section 5.1:
      - `paddock worktree create <slug> [--prefix feat] [--base REF] [--plan-path PATH] [--copy PATH ...] [--no-codegraph] [--cwd DIR] [--json]` (support repeated or multi-arg `--copy` via `action="extend", nargs="+"`).
      - `paddock worktree list [--cwd DIR] [--json]`
      - `paddock worktree remove <slug> [--delete-branch] [--cwd DIR] [--json]`
    - Print JSON when `--json` is passed, or concise human-readable summary lines (including `branch_delete_warning` if emitted); return exit `1` on errors.
  - Update `tests/test_paddock_cli.py` (replacing the `tests/test_cli_installer.py` placeholder in Spec Section 7 because `tests/test_paddock_cli.py` is the repository's existing CLI and installer test suite):
    - Update `test_models_delegation_and_memory_absent_from_public_cli` to include `"worktree"` in expected subcommand choices and `--help` output while continuing to assert `"memory"` is absent and rejected.
    - Extend `test_wheel_bundle_fallback_and_verify_bundle_integrity` to assert `verify_bundle_integrity` catches missing `platforms/{antigravity,claude}/scripts/paddock_core/{worktree.py,worktree_mcp_server.py}`.
    - Add tests for `paddock worktree create`, `list`, and `remove` covering `--json` and text output, `--prefix`, `--base`, `--plan-path`, `--copy`, `--no-codegraph`, `--delete-branch` (clean delete + unmerged warning), dirty worktree refusal, and invalid slug failure.
- **Acceptance:**
  - `python -m pytest -q tests/test_paddock_cli.py` exits `0` with all tests passing and zero skipped assertions.
  - `python -m paddock.cli worktree --help` exits `0` listing `create`, `list`, and `remove`.

### Task 7 — Update Orchestration Docs, Final Sync, Full Test Suite & Wheel Bundle Verification

- **Agent:** `paddock-implementer`
- **Files:** `platforms/antigravity/rules/AGENTS.md`, `platforms/antigravity/skills/orchestrating/SKILL.md`, `platforms/claude/skills/orchestrating/SKILL.md`, `core/skills/releasing/SKILL.md`, `core/specs/loop-contract.md`, `README.md`, `platforms/antigravity/skills/releasing/SKILL.md`, `platforms/claude/skills/releasing/SKILL.md`
- **Scope:**
  - Update `platforms/antigravity/rules/AGENTS.md`, `platforms/antigravity/skills/orchestrating/SKILL.md`, `platforms/claude/skills/orchestrating/SKILL.md` (quoting `description:` YAML frontmatter cleanly so `claude plugin validate` parses it without syntax error), `core/skills/releasing/SKILL.md` (keeping zero Claude-specific forbidden tokens such as `~/.claude`, `superpowers:`, `WebFetch`, `WebSearch`, `NotebookEdit`), `core/specs/loop-contract.md`, and `README.md`:
    - Document `paddock-worktree` MCP workflow (`worktree_create`, `worktree_list`, `worktree_remove`): main orchestrator calls `worktree_create` directly after Gate 2 approval (or delegates to fast-tier `generalist`, which remains shell-free) and passes `worktree_path` into `build_plan.py` (`Args.worktree`), `swarm`, or Claude `builder`.
    - Document `worktree_remove` `ask` prompt + non-force safety (`branch -d` only, never `--force` or `-D`) and `paddock worktree <create|list|remove>` CLI parity/fallback.
    - Update `README.md` repository layout (`core/git/worktree.py`, `core/git/worktree_mcp_server.py`) and document `paddock worktree` CLI commands.
  - Run `python scripts/sync.py` to propagate `core/skills/releasing/SKILL.md` into both platform adapters.
  - Run full end-to-end checks (using `BypassSandbox: true` / temp dir if `uv` hits Windows sandbox cache restrictions, and leaving zero untracked `dist/` artifacts in the worktree):
    1. `python scripts/sync.py --check`
    2. `uv run --with pytest pytest -q` (and `python -m pytest -q`)
    3. `uv build --wheel --out-dir <temp_dist>` and inspect the built `.whl` with Python `zipfile` to verify `paddock/_bundle/` contains `core/git/__init__.py`, `core/git/worktree.py`, `core/git/worktree_mcp_server.py`, `platforms/antigravity/scripts/paddock_core/worktree.py`, `platforms/antigravity/scripts/paddock_core/worktree_mcp_server.py`, `platforms/claude/scripts/paddock_core/worktree.py`, and `platforms/claude/scripts/paddock_core/worktree_mcp_server.py` with zero `__pycache__`, `*.pyc`, `*.pyo`, or `.pytest_cache` entries.
- **Acceptance:**
  - `python scripts/sync.py --check` prints `OK: zero sync drift` and exits `0`.
  - `uv run --with pytest pytest -q` exits `0` with all tests passing and zero skipped assertions.
  - Wheel bundle inspection exits `0` verifying all `worktree.py` and `worktree_mcp_server.py` assets inside `paddock/_bundle/` and zero cache/bytecode entries, and `git status --short` shows no stray `dist/` directory.
