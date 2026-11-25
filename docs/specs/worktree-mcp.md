# Paddock Worktree MCP Server, Cheap-Model Routing & CLI Spec

Date: 2026-11-25
Status: Draft — Pending User Approval (Gate 1)
References: [paddock-spec.md](./paddock-spec.md), [agent-models.md](./agent-models.md), [release-installer.md](./release-installer.md), [loop-contract.md](../../core/specs/loop-contract.md), [releasing/SKILL.md](../../core/skills/releasing/SKILL.md)

---

## 1. Intent & Locked Decisions (from `/grill-me`)

Paddock enforces GitHub Flow and worktree isolation via `branch-guard` (`PreToolUse` hooks in `agy_hooks.py` and `claude_hooks.py`), blocking direct source edits while on `main` or `master`. At the same time, modern agent terminal sandboxes (Antigravity's terminal sandbox, Claude Code `/sandbox`, Codex `workspace-write`) mount or protect `.git/` as read-only to prevent tampering with `.git/hooks/` and `.git/config`. This creates a two-sided deadlock after Plan Approval (Gate 2): agents cannot edit source files on `main`, and cannot run `.git/info/exclude` edits or `git worktree add` inside the terminal sandbox.

This specification establishes a cross-platform host-side MCP worktree service, cheap-model routing, and CLI parity:

1. **Preserve `branch-guard` on all platforms:** Keep `branch-guard` active in both Antigravity (`agy_hooks.py`) and Claude Code (`claude_hooks.py`) to maintain GitHub Flow and parallel worktree isolation.
2. **Separate host-side stdio MCP server (`paddock-worktree`):** Backed by shared stdlib module `core/git/worktree.py` and `core/git/worktree_mcp_server.py` (distinct filename from `core/memory/mcp_server.py` so both coexist inside flat `platforms/<plat>/scripts/paddock_core/`).
3. **Scoped worktree lifecycle only:** Expose strictly `worktree_create`, `worktree_list`, and `worktree_remove` (no arbitrary git/shell execution; commit, push, and PR workflows remain out of scope).
4. **Callers & cheap-model routing:**
   - Main orchestrator calls `worktree_*` directly right after Gate 2 approval (zero extra subagent turns) and passes `worktree_path` into `build_plan.py` (`Args.worktree`), `swarm`, or Claude `builder`.
   - `generalist` (`fast` tier: `gemini-4-hydrogen-medium`/`flash` on Antigravity; `haiku` on Claude Code; still shell-free) gains `mcp` capability for delegated worktree operations.
5. **Turnkey `worktree_create`:** Ensures `.worktrees/` in local `.git/info/exclude`, idempotently creates or reuses `.worktrees/<slug>`, auto-copies untracked `docs/specs/<slug>*.md` and `docs/plans/<slug>*.md` (plus optional `copy_paths`) via `shutil.copy2` (preserving `mtime` so Gate 2 approval checks never false-deny), and runs non-fatal `codegraph init -y` when `<repo_root>/.codegraph/` exists.
6. **Approval gating:** `worktree_list` and `worktree_create` run seamlessly (with optional Gate 2 verification when `plan_path` is passed); `worktree_remove` is strictly non-force and always triggers a `PreToolUse` `ask` confirmation prompt on both platforms.
7. **Cross-platform & CLI parity:** Sync `core/git/worktree.py` and `core/git/worktree_mcp_server.py` into both `platforms/antigravity/` and `platforms/claude/`, register `paddock-worktree` in both platform MCP configs, and add `paddock worktree <create|list|remove>` CLI subcommands (`--json`) in `paddock/cli.py`.

---

## 2. Core Module Design (`core/git/worktree.py` & `core/git/__init__.py`)

Implemented in pure Python 3.12 stdlib (zero third-party runtime dependencies), usable both directly in a repository checkout (`core.git.worktree`) and as a standalone synced module inside `platforms/<plat>/scripts/paddock_core/worktree.py`.

### 2.1 Shared Root & Validation Helpers

- **`resolve_repo_root(cwd)`:** Resolves the main checkout root via `git -C <cwd> rev-parse --path-format=absolute --git-common-dir` (matching `core/memory/memory.py`). Also resolves `<git_common_dir>` explicitly for `.git/info/exclude` writes.
- **Slug validation:** `SLUG_RE = r"^[a-z0-9][a-z0-9._-]{0,63}$"` (rejects `..`, `/`, `\`, leading `-`, empty strings, non-ASCII, and control characters).
- **Prefix allowlist:** `VALID_PREFIXES = ("feat", "fix", "docs", "refactor", "chore", "test")`.
- **Ref validation:** Base ref strings (when provided) must match a safe git ref regex and never start with `-`.
- **Strict containment & symlink/junction rejection:**
  - Target worktree path must resolve strictly inside `<repo_root>/.worktrees/<slug>`.
  - Every path component from `<repo_root>` down to target is checked against symlinks and Windows directory junctions (reusing the same safety pattern as `core/config/agents.py`).
- **`copy_paths` validation:** Accepts relative paths only; rejects `..`, absolute paths, symlinks/junctions, paths escaping `<repo_root>`, and any path inside `.git/`.

### 2.2 Operations & Return Schemas

#### `ensure_worktree_exclude(repo_root) -> bool`
- Locates `<git_common_dir>/info/exclude` (creating `info/` if absent).
- Checks if `.worktrees/` (or `.worktrees`) is already present as a line.
- If missing, atomically appends `.worktrees/` while preserving existing content and line endings; returns `True` if updated, `False` if already present.
- Never edits tracked `.gitignore`, `.git/hooks/`, or `.git/config`.

#### `create_worktree(cwd, slug, *, prefix="feat", base=None, plan_path=None, copy_paths=None, init_codegraph=True) -> dict`
- Validates `slug`, `prefix`, optional `base`, and optional `copy_paths` / `plan_path`.
- Calls `ensure_worktree_exclude(repo_root)`.
- Target branch: `<prefix>/<slug>`.
- Base ref: explicit `base`, or auto-detected (`main` if local/HEAD ref exists, else `master` if exists, else `HEAD`).
- **Idempotent create/reuse logic:**
  - If `<repo_root>/.worktrees/<slug>` already exists as a registered worktree checked out on `<prefix>/<slug>`, reuse it (`created=False`, `reused=True`) and still copy any missing untracked docs.
  - If `.worktrees/<slug>` exists on a *different* branch or is a non-worktree directory, fail cleanly with a clear error (never overwrite or force-reset).
  - If local branch `<prefix>/<slug>` already exists and is not checked out elsewhere, attach it via `git worktree add <path> <branch>` (`created=True`, `reused=False`, `reused_branch=True`); if checked out in another worktree, fail cleanly naming that worktree.
  - Otherwise, create via `git worktree add -b <prefix>/<slug> <path> <base>`.
- **Auto-copy untracked docs:**
  - Discovers untracked files in `<repo_root>` matching `docs/specs/<slug>*.md` and `docs/plans/<slug>*.md`, plus any explicit `copy_paths` and `plan_path` (if untracked or missing in worktree).
  - Copies into `<worktree_path>` using `shutil.copy2` (preserving `mtime` so Gate 2 approval mtime checks remain valid).
  - Skips destination if it already exists with identical content; never overwrites newer or tracked edits in the worktree.
- **Optional CodeGraph initialization:**
  - If `init_codegraph=True`, `(repo_root / ".codegraph").is_dir()`, `shutil.which("codegraph")` is present, and `(worktree_path / ".codegraph")` does not exist, runs `codegraph init -y` in `worktree_path` with a 30s timeout (non-fatal on failure).
  - Reports `codegraph_status`: `"initialized" | "already_initialized" | "skipped_no_source_index" | "skipped_disabled" | "skipped_missing_cli" | "failed: <reason>"`.
- **Return JSON:**
  ```json
  {
    "repo_root": "...",
    "worktree_path": "...",
    "branch": "feat/<slug>",
    "base": "main",
    "created": true,
    "reused": false,
    "exclude_updated": false,
    "copied_files": ["docs/specs/<slug>.md", "docs/plans/<slug>.md"],
    "codegraph_status": "initialized"
  }
  ```

#### `list_worktrees(cwd) -> dict`
- Parses `git worktree list --porcelain` into:
  ```json
  {
    "repo_root": "...",
    "worktrees": [
      {
        "path": "...",
        "head": "...",
        "branch": "...",
        "bare": false,
        "detached": false,
        "locked": false,
        "prunable": false,
        "is_main": true,
        "is_paddock_managed": false,
        "slug": null
      }
    ]
  }
  ```

#### `remove_worktree(cwd, slug, *, delete_branch=False) -> dict`
- Validates `slug` and confines removal strictly to registered worktrees at `<repo_root>/.worktrees/<slug>` (never removes main checkout or external worktrees).
- Runs strictly non-force `git worktree remove <path>` (fails cleanly if dirty or containing untracked files).
- If `delete_branch=True` and the worktree's branch is not `main` or `master`, runs strictly non-force `git branch -d <branch>` (fails cleanly if unmerged, never `-D`).
- **Return JSON:**
  ```json
  {
    "repo_root": "...",
    "worktree_path": "...",
    "slug": "<slug>",
    "removed": true,
    "branch": "feat/<slug>",
    "branch_deleted": true,
    "branch_delete_warning": null
  }
  ```
  *(If worktree removal succeeds but `git branch -d` refuses an unmerged branch, `removed=true`, `branch_deleted=false`, and `branch_delete_warning` explains why without raising or running `-D`.)*

---

## 3. Stdio MCP Server (`core/git/worktree_mcp_server.py`)

Mirrors `core/memory/mcp_server.py`:
- Pure Python stdlib newline-delimited JSON-RPC 2.0 stdio transport.
- Supported protocol versions: `{"2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"}`.
- `SERVER_INFO = {"name": "paddock-worktree", "version": "0.3.0"}` (checked by `check_version_parity`).
- Exposes 3 tools (`worktree_create`, `worktree_list`, `worktree_remove`), each requiring explicit `cwd`.
- Registered as `"paddock-worktree"` alongside `"paddock-mem"`:
  - `platforms/antigravity/mcp_config.json`: `uv run --no-project --script scripts/paddock_core/worktree_mcp_server.py` (`PYTHONUTF8=1`).
  - `platforms/claude/.mcp.json`: `uv run --no-project --script ${CLAUDE_PLUGIN_ROOT}/scripts/paddock_core/worktree_mcp_server.py` (`PYTHONUTF8=1`).

---

## 4. Platform Hooks, Model Routing & Agent Capabilities

### 4.1 Canonical Agent Capabilities (`core/config/agents.py` & `core/agents/generalist.md`)

- Add `"mcp"` to `VALID_CAPABILITIES` in `core/config/agents.py`.
- Map `"mcp"` in `CAPABILITY_TOOL_MAP`:
  - `antigravity`: `("call_mcp_tool",)`
  - `claude`: `("mcp__plugin_paddock_paddock-worktree__worktree_create", "mcp__plugin_paddock_paddock-worktree__worktree_list", "mcp__plugin_paddock_paddock-worktree__worktree_remove")` *(exact Claude Code plugin MCP tool frontmatter naming to be verified against Claude Code runtime docs during planning/implementation)*.
- Update `core/agents/generalist.md`:
  - `capabilities: [read, edit, write, search, web, mcp]`
  - Keep `disallowed: [shell, delegate]` (`fast` tier: `gemini-4-hydrogen-medium`/`flash` on Antigravity; `haiku` on Claude Code).

### 4.2 Antigravity Hooks (`platforms/antigravity/hooks.json` & `platforms/antigravity/scripts/agy_hooks.py`)

- Keep `_handle_branch_guard` unchanged.
- Add `PreToolUse` matcher `call_mcp_tool` in `platforms/antigravity/hooks.json`.
- In `agy_hooks.py`, intercept `call_mcp_tool` when `ServerName` is `paddock_paddock-worktree` or `paddock-worktree`:
  - `worktree_list`: `{"decision": "allow"}`.
  - `worktree_create`: if `Arguments.plan_path` is provided, validate via `_validate_plan_file` (Approval Gate 2); otherwise `{"decision": "allow"}`.
  - `worktree_remove`: return `{"decision": "ask", "reason": "Confirm removing worktree '.worktrees/<slug>' (delete_branch=<bool>). Non-force only."}`.
- Update `_handle_run_workflow_gate` in `agy_hooks.py` so relative `Args.plan_path` resolves cleanly whether checked against parent repo or `Args.worktree`.

### 4.3 Claude Code Hooks (`platforms/claude/hooks/hooks.json` & `platforms/claude/scripts/claude_hooks.py`)

- Keep `guard` and `branch-guard` unchanged.
- Add `PreToolUse` matcher `mcp__.*paddock-worktree__.*` in `platforms/claude/hooks/hooks.json`.
- Add `_ask(reason)` helper returning `hookSpecificOutput` with `permissionDecision: "ask"`.
- In `claude_hooks.py`, intercept `mcp__.*paddock-worktree__(worktree_create|worktree_list|worktree_remove)`:
  - `worktree_list`: allow (`{}`).
  - `worktree_create`: if `tool_input.plan_path` is provided, validate Approval Gate 2 via `_check_approval_gate` and return `_deny(...)` on violation; otherwise allow (`{}`).
  - `worktree_remove`: return `_ask(...)` displaying target `slug` and `delete_branch`.

### 4.4 Orchestrator & Workflow Integration

- Main orchestrator calls `worktree_create` directly after Gate 2 approval (or delegates to fast-tier `generalist`), then passes returned `worktree_path` into `build_plan.py` (`Args.worktree`), `swarm`, or Claude `builder`.
- Update `AGENTS.md`, `platforms/antigravity/skills/orchestrating/SKILL.md`, `platforms/claude/skills/orchestrating/SKILL.md`, `core/skills/releasing/SKILL.md`, and `core/specs/loop-contract.md` to document `paddock-worktree` MCP workflow and `paddock worktree` CLI fallback.

---

## 5. CLI Parity, Sync & Installer

### 5.1 CLI (`paddock/cli.py`)

Add `paddock worktree` subcommand group:
- `paddock worktree create <slug> [--prefix feat] [--base REF] [--plan-path PATH] [--copy PATH ...] [--no-codegraph] [--cwd DIR] [--json]`
- `paddock worktree list [--cwd DIR] [--json]`
- `paddock worktree remove <slug> [--delete-branch] [--cwd DIR] [--json]`

### 5.2 Sync (`scripts/sync.py`)

- Add `CORE_GIT_FILES = ("worktree.py", "worktree_mcp_server.py")`.
- Sync `core/git/*` into `platforms/antigravity/scripts/paddock_core/` and `platforms/claude/scripts/paddock_core/`.
- Extend `check_version_parity` to include `core/git/worktree_mcp_server.py` and its synced copies.

### 5.3 Installer (`paddock/installer.py`)

- Add `worktree.py` and `worktree_mcp_server.py` under `platforms/antigravity/scripts/paddock_core/` to `_REQUIRED_ANTIGRAVITY_ASSETS`.
- Add `worktree.py` and `worktree_mcp_server.py` under `platforms/claude/scripts/paddock_core/` to `_REQUIRED_BUNDLE_ASSETS`.

---

## 6. Security

- **Command injection prevention:** All `git` and `codegraph` invocations use `subprocess.run([...], shell=False)` with explicit `--` argument separators where applicable; slugs, branch prefixes, and base refs are strictly validated against allowlist regexes and never allowed to begin with `-`.
- **Path traversal & symlink/junction confinement:** Target worktrees are strictly confined under `<repo_root>/.worktrees/<slug>`; `copy_paths` are strictly confined under `<repo_root>` and outside `.git/`; symlink and Windows junction checks are enforced across path components.
- **Protected `.git/` boundaries:** The MCP server only writes `.worktrees/` to `<git_common_dir>/info/exclude` and runs narrow `git worktree add/list/remove` + `git branch -d`. It never modifies `.git/hooks/`, `.git/config`, or tracked `.gitignore`, never exposes arbitrary git subcommands/flags, never passes `--force` or `-D`, and never deletes `main`/`master`.
- **Least-privilege delegation:** `generalist` remains shell-free (`disallowed: [shell, delegate]`); `worktree_remove` always triggers a `PreToolUse` `ask` confirmation prompt; `paddock-worktree` runs as an independent MCP server that users can inspect or disable independently of `paddock-mem`.

---

## 7. Verification & Acceptance Criteria

- **New unit & integration tests:**
  - `tests/test_worktree_core.py`: slug/prefix/ref validation, symlink/junction & traversal rejection, `.git/info/exclude` idempotency, worktree create/reuse/attach/conflict handling, `shutil.copy2` mtime preservation, optional CodeGraph init, `list_worktrees`, and non-force `remove_worktree` + unmerged `branch -d` warning.
  - `tests/test_worktree_mcp.py`: JSON-RPC 2.0 `initialize`, `tools/list`, `tools/call` for all 3 tools, error handling, and `SERVER_INFO` parity.
- **Updated test suites:**
  - `tests/test_agy_hooks.py` and `tests/test_claude_hooks.py`: `call_mcp_tool` / `mcp__.*paddock-worktree__.*` routing (`allow`, Gate 2 `deny`, `ask` on `worktree_remove`), plus `Args.worktree` + relative `Args.plan_path` resolution.
  - `tests/test_agents_standard.py`: `"mcp"` capability compilation into Antigravity & Claude `generalist` profiles while preserving `disallowed` shell/delegate guards.
  - `tests/test_sync_drift.py` and `tests/test_cli_installer.py`: `core/git/*` sync parity, `check_version_parity`, required bundle assets, and `paddock worktree` CLI subcommands.
- **End-to-end checks:**
  - `python scripts/sync.py --check` passes with zero drift.
  - `uv run --with pytest pytest -q` passes with zero failures or skipped assertions.
  - Wheel build verifies `paddock/_bundle/` includes all required `worktree.py` and `worktree_mcp_server.py` assets.
