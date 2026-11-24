# Paddock

Multi-platform orchestration loop and unified `~/.paddock/` typed memory suite for AI coding assistants (**Antigravity**, **Claude Code**, and Phase 2 targets **Codex**, **GitHub Copilot**, and **Pi**).

## Layout

```text
paddock/
├── .claude-plugin/marketplace.json   # Local Claude Code marketplace -> ./platforms/claude
├── core/
│   ├── agents/                       # Canonical specialist definitions compiled per platform
│   ├── config/
│   │   ├── models.py                 # Versioned multi-provider model config engine & CLI
│   │   └── agents.py                 # Canonical agent parser, compiler, & apply helper
│   ├── memory/
│   │   ├── memory.py                 # Typed memory engine, validator, indexer, CLI, importer
│   │   └── mcp_server.py             # Zero-dependency stdlib JSON-RPC 2.0 stdio MCP server
│   ├── skills/                       # Agnostic source-of-truth skills (synced into adapters)
│   │   └── paddock-models/           # /paddock-models interactive model configuration skill
│   └── specs/loop-contract.md        # Canonical two-lane orchestration contract
├── platforms/
│   ├── antigravity/                  # Phase 1 — native Antigravity plugin + build_plan.py workflow
│   └── claude/                       # Phase 1 — native Claude Code plugin + lifecycle hooks
├── scripts/
│   ├── sync.py                       # Syncs skills, paddock_core/, & compiled agents (no symlinks)
│   ├── install.py                    # Personal-machine install/uninstall helper
│   └── models.py                     # CLI wrapper for core/config/models.py
└── tests/                            # pytest suite (core, MCP, models/agents, sync drift, hooks)
```

## Quick Start

- **Sync core skills, config modules, and compiled agents into platform adapters:**
  ```bash
  python scripts/sync.py
  python scripts/sync.py --check
  ```
- **Run test suite:**
  ```bash
  python -m pytest -q
  ```
- **Install adapters (dry-run first):**
  ```bash
  python scripts/install.py --platform all --dry-run
  python scripts/install.py --platform antigravity
  ```
- **One-time read-only migration from legacy `~/.claude/` memory:**
  ```bash
  uv run --no-project --script core/memory/memory.py import-claude --dry-run --cwd /path/to/repo
  ```

## Model Configuration (`/paddock-models` & `scripts/models.py`)

Configure Paddock's versioned multi-platform model settings (`schema_version: 1`) across all six roles (`generalist`, `implementer`, `ui`, `planner`, `builder`, `verifier`) and five platforms (`antigravity`, `claude`, `codex`, `copilot`, `pi`) without polluting target repositories.

- **Interactive chat workflow:** Run `/paddock-models` in Antigravity or Claude Code to inspect current assignments, pick `global` or `project` scope, apply changes, and refresh effective output.
- **CLI quick-start (`uv run --no-project --script scripts/models.py ...` or `python scripts/models.py ...`):**
  ```bash
  # Inspect resolved models, efforts, Antigravity aliases, and source layers
  uv run --no-project --script scripts/models.py show --cwd /path/to/repo --platform antigravity

  # Apply a built-in preset (budget | balanced | quality) globally or per-project
  uv run --no-project --script scripts/models.py preset quality --scope global --cwd /path/to/repo

  # Switch a multi-provider platform to another built-in catalog
  uv run --no-project --script scripts/models.py set-provider antigravity anthropic --scope global
  uv run --no-project --script scripts/models.py set-provider copilot openai --scope project --cwd /path/to/repo

  # Change one role's abstract tier (fast | workhorse | flagship) and optional effort (low | medium | high)
  uv run --no-project --script scripts/models.py set-role implementer flagship --effort high --scope project --cwd /path/to/repo

  # Customize a platform tier's concrete model and effort
  uv run --no-project --script scripts/models.py set-tier claude workhorse --model sonnet --effort high --scope global

  # Pin or clear a per-platform, per-role override
  uv run --no-project --script scripts/models.py set-override antigravity ui --model gemini-4-neon --effort high --scope project --cwd /path/to/repo
  uv run --no-project --script scripts/models.py set-override antigravity ui --clear --scope project --cwd /path/to/repo

  # Recompile installed static agent profiles from global ~/.paddock/models.json
  uv run --no-project --script scripts/models.py apply --platform claude --dry-run
  uv run --no-project --script scripts/models.py apply --platform all

  # Remove customizations and restore balanced defaults
  uv run --no-project --script scripts/models.py reset --scope project --cwd /path/to/repo
  ```
  *(Pass `--json` to any subcommand for structured output; `apply` also accepts `--home <dir>` and `--target <dir>`.)*

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

On **Antigravity**, `models.py show --platform antigravity` reports both dispatch forms:
- **`swarm_model` (`swarm`):** Exact `<model>-<effort>` IDs (for example `gemini-4-hydrogen-medium`, `gemini-4-neon-medium`, `gemini-4-carbon-medium`, or `claude-sonnet-5-5-medium`) injected automatically by `agy_hooks.py` (`PreToolUse` `overwrite` on `swarm` `action="add"`).
- **`subagent_alias` (`invoke_subagent` / `workflows/build_plan.py`):** Safe 4-value enum fallback (`fast` -> `flash`, `workhorse` -> `pro`, `flagship` -> `inherit`, or explicit `inherit` / `flash_lite` / `flash` / `pro` passthrough) used by `build_plan.py` and `invoke_subagent` hook injection.

### Global vs. Per-Project Precedence

- **Global config:** `~/.paddock/models.json` (or `$PADDOCK_HOME/models.json`). Used as the baseline across repositories and as the input when `models.py apply` updates installed static agent profiles.
- **Per-project config:** `~/.paddock/projects/<repo-slug>/models.json` (using `memory.py`'s `repo_slug(cwd)` so a main checkout and its `.worktrees/*` share config). Unmodified top-level keys may be omitted; partial objects merge cleanly over the global layer.
- **Resolution order (highest to lowest):**
  1. Call-time explicit argument (e.g., `Args.models` in `build_plan.py`, explicit non-`"inherit"` `Model` in `invoke_subagent`, or explicit `model` in `swarm`)
  2. Project platform override (`project` `overrides.<platform>.<role>`)
  3. Project role tier (`project` `roles.<role>` / `preset`)
  4. Global platform override (`global` `overrides.<platform>.<role>`)
  5. Global role tier (`global` `roles.<role>` / `preset`)
  6. Built-in `balanced` defaults

On Claude Code, `apply` writes global defaults into installed static `agents/*.md` profiles, while `claude_hooks.py` reports effective `model/effort` at `SessionStart` and surfaces project-level override mismatches via non-blocking `additionalContext` on `PreToolUse` (`Agent`).

## Environment Variables

- `PADDOCK_HOME`: Override unified memory and model config root (default: `~/.paddock`).
- `PADDOCK_SOLO=1`: Disable enforcement hooks for solo sessions.
