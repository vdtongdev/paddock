# Paddock

Multi-platform orchestration loop and unified `~/.paddock/` typed memory suite for AI coding assistants (**Antigravity**, **Claude Code**, and Phase 2 targets **Codex**, **GitHub Copilot**, and **Pi**).

## Install

**Prerequisites:** Python `3.12+` and [`uv`](https://docs.astral.sh/uv/) (zero third-party runtime dependencies).

### Recommended (All Platforms & Antigravity — 2 Steps, No Clone Required)

```bash
uv tool install git+https://github.com/vdtongdev/paddock.git
paddock install
```

With no target argument, `paddock install` auto-detects installed environments (Antigravity and Claude Code). You can also target a platform explicitly:

```bash
paddock install antigravity
paddock install claude
paddock install all
```

- **Antigravity:** Installs the pre-synced plugin bundle in-place to `~/.gemini/config/plugins/paddock` and applies your global `~/.paddock/models.json` (or built-in `balanced` defaults).
- **Claude Code:** Automatically runs `claude plugin marketplace add vdtongdev/paddock` (or `marketplace update paddock`) and `claude plugin install paddock@paddock`, then compiles installed agent profiles from `~/.paddock/models.json`. If the `claude` CLI is not on `PATH` or `--dry-run` is passed, it prints the exact commands to run.
- Useful flags: `--platform <antigravity|claude|all>`, `--dry-run`, `--target <dir>`, `--home <dir>`, `--json`.

**Zero-install one-liner (`uvx`):**

```bash
uvx --from git+https://github.com/vdtongdev/paddock.git paddock install
```

### Claude Code Native Marketplace Alternative

If you only want the Claude Code plugin directly inside Claude Code:

```bash
claude plugin marketplace add vdtongdev/paddock
claude plugin install paddock@paddock
```

If you later customize global model settings, run `paddock install claude` or `paddock models apply --platform claude` to update installed static agent profiles.

### Check Status

Inspect the CLI version, bundle vs. repo source mode, installed platform plugins, active global/project model config, and detected tools (`uv`, `claude`, `git`, `gh`):

```bash
paddock --version
paddock status
paddock status --json
```

*(Accepts `--cwd <path>`, `--home <dir>`, and `--json`.)*

### Update

Upgrade both the `paddock` CLI and detected platform plugins to the latest GitHub Release tag (`vX.Y.Z`):

```bash
paddock update
```

Common options:

```bash
# Check whether a newer GitHub Release is available without modifying files
paddock update --check

# Install a specific SemVer release tag or track the main branch
paddock update --version vX.Y.Z
paddock update --main

# Resync platform plugins from the currently installed package without running uv
paddock update --plugins-only
```

*(Also supports `--platform <antigravity|claude|all>`, `--dry-run`, `--home <dir>`, and `--json`.)*

> **Windows note:** Overwriting a running `paddock.exe` via `uv tool install --force` can trigger a file-in-use or access lock error (`WinError 5` / `WinError 32`). When that happens, `paddock update` leaves your installed plugins untouched and prints the two recovery commands to run directly in your terminal:
>
> ```bash
> uv tool install --force git+https://github.com/vdtongdev/paddock.git@<ref>
> paddock install
> ```

### Uninstall

Remove installed platform plugins (auto-detected, or pass `antigravity`, `claude`, or `all`):

```bash
paddock uninstall
```

`paddock uninstall` removes `~/.gemini/config/plugins/paddock` and/or runs `claude plugin uninstall paddock@paddock` (printing the command if `claude` is absent). It **never** deletes `~/.paddock/`, preserving your typed memory database and `models.json` configurations. *(Supports `--platform <antigravity|claude|all>`, `--dry-run`, `--home <dir>`, and `--json`.)*

## Model Configuration

Configure Paddock's versioned multi-platform model settings (`schema_version: 1`) across all six roles (`generalist`, `implementer`, `ui`, `planner`, `builder`, `verifier`) and five platforms (`antigravity`, `claude`, `codex`, `copilot`, `pi`) without polluting target repositories.

- **Interactive chat workflow:** Run `/paddock-models` in Antigravity or Claude Code to inspect current assignments, pick `global` or `project` scope, apply changes, and refresh effective output.
- **CLI quick-start (`paddock models ...`):**
  ```bash
  # Inspect resolved models, efforts, Antigravity aliases, and source layers
  paddock models show --cwd /path/to/repo --platform antigravity

  # Apply a built-in preset (budget | balanced | quality) globally or per-project
  paddock models preset quality --scope global --cwd /path/to/repo

  # Switch a multi-provider platform to another built-in catalog
  paddock models set-provider antigravity anthropic --scope global
  paddock models set-provider copilot openai --scope project --cwd /path/to/repo

  # Change one role's abstract tier (fast | workhorse | flagship) and optional effort (low | medium | high)
  paddock models set-role implementer flagship --effort high --scope project --cwd /path/to/repo

  # Customize a platform tier's concrete model and effort
  paddock models set-tier claude workhorse --model sonnet --effort high --scope global

  # Pin or clear a per-platform, per-role override
  paddock models set-override antigravity ui --model gemini-4-neon --effort high --scope project --cwd /path/to/repo
  paddock models set-override antigravity ui --clear --scope project --cwd /path/to/repo

  # Recompile installed static agent profiles from global ~/.paddock/models.json
  paddock models apply --platform claude --dry-run
  paddock models apply --platform all

  # Remove customizations and restore balanced defaults
  paddock models reset --scope project --cwd /path/to/repo
  ```
  *(Pass `--json` to any subcommand for structured output; `apply` also accepts `--home <dir>` and `--target <dir>`, and `reset` accepts `--scope global|project|all`.)*

### Multi-Provider Tier Catalogs & Antigravity Dispatch

Built-in presets (`budget`, `balanced` default, `quality`) map abstract tiers (`fast`, `workhorse`, `flagship`) and reasoning `effort` (`low`, `medium`, `high`; default `medium`) onto each platform's active provider catalog:

| Platform | Providers (`default` first) | `fast` | `workhorse` | `flagship` |
| :--- | :--- | :--- | :--- | :--- |
| `antigravity` | **`google`** (default) | `gemini-4-hydrogen` | `gemini-4-neon` | `gemini-4-carbon` |
| `antigravity` | `anthropic` (hybrid) | `gemini-4-hydrogen` | `claude-sonnet-5-5` | `claude-opus-5-5` |
| `antigravity` | `native` (enum, no effort) | `flash` | `pro` | `inherit` |
| `claude` | **`anthropic`** (default) | `haiku` | `sonnet` | `opus` |
| `codex` | **`openai`** (default) | `gpt-6-luna` | `gpt-6.2-sol` | `gpt-6.2-astra` |
| `copilot` | **`anthropic`** (default) | `claude-haiku-5.5` | `claude-sonnet-5.5` | `claude-opus-5.5` |
| `copilot` | `openai` | `gpt-6-luna` | `gpt-6.2-sol` | `gpt-6.2-astra` |
| `copilot` | `google` | `gemini-4-hydrogen` | `gemini-4-neon` | `gemini-4-carbon` |
| `pi` | **`anthropic`** (default) | `anthropic/claude-haiku-5-5` | `anthropic/claude-sonnet-5-5` | `anthropic/claude-opus-5-5` |
| `pi` | `openai` | `openai/gpt-6-luna` | `openai/gpt-6.2-sol` | `openai/gpt-6.2-astra` |
| `pi` | `google` | `google/gemini-4-hydrogen` | `google/gemini-4-neon` | `google/gemini-4-carbon` |

On **Antigravity**, `paddock models show --platform antigravity` reports both dispatch forms:
- **`swarm_model` (`swarm`):** Exact `<model>-<effort>` IDs (for example `gemini-4-hydrogen-medium`, `gemini-4-neon-medium`, `gemini-4-carbon-medium`, or `claude-sonnet-5-5-medium`) or bare aliases verbatim injected automatically by `agy_hooks.py` (`PreToolUse` `overwrite` on `swarm` `action="add"`).
- **`subagent_alias` (`invoke_subagent` / `workflows/build_plan.py`):** Safe 4-value enum fallback (`fast` -> `flash`, `workhorse` -> `pro`, `flagship` -> `inherit`, or explicit `inherit` / `flash_lite` / `flash` / `pro` passthrough) used by `build_plan.py` and `invoke_subagent` hook injection.

### Global vs. Per-Project Precedence

- **Global config:** `~/.paddock/models.json` (or `$PADDOCK_HOME/models.json`). Used as the baseline across repositories and as the input when `paddock models apply` updates installed static agent profiles.
- **Per-project config:** `~/.paddock/projects/<repo-slug>/models.json` (using a worktree-shared repository slug so a main checkout and its `.worktrees/*` share config). Unmodified top-level keys may be omitted; partial objects merge cleanly over the global layer.
- **Resolution order (highest to lowest):**
  1. Call-time explicit argument (e.g., `Args.models` in `build_plan.py`, explicit non-`"inherit"` `Model` in `invoke_subagent`, or explicit `model` in `swarm`)
  2. Project platform override (`project` `overrides.<platform>.<role>`)
  3. Project role tier (`project` `roles.<role>` / `preset`)
  4. Global platform override (`global` `overrides.<platform>.<role>`)
  5. Global role tier (`global` `roles.<role>` / `preset`)
  6. Built-in `balanced` defaults

On Claude Code, `apply` writes global defaults into installed static `agents/*.md` profiles, while `claude_hooks.py` reports effective `model/effort` at `SessionStart` and surfaces project-level override mismatches via non-blocking `additionalContext` on `PreToolUse` (`Agent`).

## Worktree Isolation (`paddock-worktree` MCP & `paddock worktree` CLI)

Paddock enforces GitHub Flow via `branch-guard` (`PreToolUse` hooks block direct code edits on `main`/`master`). When agent terminal sandboxes restrict writes to `.git/`, the host-side `paddock-worktree` stdio MCP server (`worktree_create`, `worktree_list`, `worktree_remove`) and `paddock worktree` CLI provide non-force `.worktrees/<slug>` lifecycle management:

- **Orchestrator & cheap-model routing:** After Gate 2 approval, the main orchestrator calls `worktree_create` directly (or delegates to fast-tier, shell-free `generalist` with `mcp` capability) and passes the returned `worktree_path` into Antigravity `workflows/build_plan.py` (`Args.worktree`), `swarm`, or Claude `builder`.
- **Turnkey setup (`worktree_create`):** Adds `.worktrees/` to local `.git/info/exclude` (never tracked `.gitignore`), idempotently creates or reuses `.worktrees/<slug>` on `<prefix>/<slug>`, copies matching untracked `docs/specs/<slug>*.md`, `docs/plans/<slug>*.md`, and optional `plan_path`/`copy_paths` preserving `mtime` (with Gate 2 checked when `plan_path` is passed), and runs non-fatal `codegraph init -y` if `.codegraph/` exists.
- **Non-force removal (`worktree_remove`):** Always triggers a `PreToolUse` `ask` confirmation prompt on both platforms, refuses dirty, untracked, main, or external checkouts, and uses non-force `git branch -d` when `delete_branch=true` (never `--force` or `-D`).
- **CLI parity/fallback (`paddock worktree ...`):**
  ```bash
  # Idempotently create or reuse .worktrees/<slug> on <prefix>/<slug> (auto-copies matching untracked specs/plans)
  paddock worktree create <slug> --prefix feat --plan-path docs/plans/<slug>.md --json

  # Optional flags: --base REF, --copy PATH ..., --no-codegraph, --cwd DIR
  paddock worktree create <slug> --base main --copy notes.md --no-codegraph

  # List registered git worktrees and Paddock-managed slugs
  paddock worktree list --cwd /path/to/repo --json

  # Non-force remove .worktrees/<slug> and optionally delete merged branch via `git branch -d`
  paddock worktree remove <slug> --delete-branch --cwd /path/to/repo --json
  ```

## Development

Contributors working on Paddock itself can clone the repository and run sync, version parity, and test checks locally:

```bash
git clone https://github.com/vdtongdev/paddock.git
cd paddock
```

### Repository Layout

```text
paddock/
├── .claude-plugin/marketplace.json   # Claude Code marketplace manifest -> ./platforms/claude
├── .github/workflows/
│   ├── ci.yml                        # Cross-platform sync check, pytest, wheel & out-of-repo smoke test
│   └── release.yml                   # Tag-verified release archives, wheel, SHA256SUMS.txt, GitHub Release
├── core/
│   ├── agents/                       # Canonical specialist definitions compiled per platform
│   ├── config/
│   │   ├── models.py                 # Versioned multi-provider model config engine & CLI
│   │   └── agents.py                 # Canonical agent parser, compiler, & apply helper
│   ├── git/
│   │   ├── worktree.py               # Git worktree isolation, exclude, & non-force cleanup
│   │   └── worktree_mcp_server.py    # Zero-dependency stdlib JSON-RPC 2.0 `paddock-worktree` MCP
│   ├── memory/
│   │   ├── memory.py                 # Typed memory engine, validator, & indexer (MCP/internal)
│   │   └── mcp_server.py             # Zero-dependency stdlib JSON-RPC 2.0 stdio MCP server
│   ├── skills/                       # Agnostic source-of-truth skills (synced into adapters)
│   │   └── paddock-models/           # /paddock-models interactive model configuration skill
│   └── specs/loop-contract.md        # Canonical two-lane orchestration contract
├── paddock/
│   ├── __init__.py                   # Package version (__version__)
│   ├── cli.py                        # Unified `paddock` CLI entrypoint
│   ├── installer.py                  # Repo/bundle source resolver, platform installer, & status
│   ├── updater.py                    # GitHub Release ref resolver & self-updating tool/plugin sync
│   └── version.py                    # Stdlib SemVer 2.0.0 validation & comparison helpers
├── platforms/
│   ├── antigravity/                  # Phase 1 — native Antigravity plugin + build_plan.py workflow
│   └── claude/                       # Phase 1 — native Claude Code plugin + lifecycle hooks
├── pyproject.toml                    # Zero-dependency Python 3.12+ package & wheel bundle config
├── scripts/
│   ├── sync.py                       # Syncs skills, paddock_core/, compiled agents, & version parity
│   ├── install.py                    # Backward-compatible installer wrapper -> paddock.installer
│   └── models.py                     # Backward-compatible models CLI wrapper -> core/config/models.py
└── tests/                            # pytest suite (core, MCP, models/agents, CLI, sync drift, hooks)
```

### Sync, Parity, and Tests

- **Sync core skills, `paddock_core/`, compiled agents, and check SemVer parity across `pyproject.toml`, `paddock/__init__.py`, plugin manifests, `mcp_server.py`, and `worktree_mcp_server.py`:**
  ```bash
  python scripts/sync.py
  python scripts/sync.py --check
  ```
- **Run test suite:**
  ```bash
  python -m pytest -q
  ```
- **Run CLI from checkout or editable install:**
  ```bash
  python -m paddock.cli status --json
  uv tool install --editable .
  ```
- **Backward-compatible contributor script wrappers:**
  - `scripts/install.py` delegates to `paddock.installer` (requires explicit `--platform`, e.g., `python scripts/install.py --platform all --dry-run`; supports `--repo`, `--target`, `--home`, `--uninstall`, `--dry-run`).
  - `scripts/models.py` delegates to `core/config/models.py` (e.g., `python scripts/models.py show --json`).

## Environment Variables

- `PADDOCK_HOME`: Override unified memory and model config root (default: `~/.paddock`).
- `PADDOCK_SOLO=1`: Disable enforcement hooks for solo sessions.
