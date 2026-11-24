# Paddock

Multi-platform orchestration loop and unified `~/.paddock/` typed memory suite for AI coding assistants (**Antigravity**, **Claude Code**, and Phase 2 targets **Codex**, **GitHub Copilot**, and **Pi**).

## Layout

```text
paddock/
├── .claude-plugin/marketplace.json   # Local Claude Code marketplace -> ./platforms/claude
├── core/
│   ├── memory/
│   │   ├── memory.py                 # Typed memory engine, validator, indexer, CLI, importer
│   │   └── mcp_server.py             # Zero-dependency stdlib JSON-RPC 2.0 stdio MCP server
│   ├── skills/                       # Agnostic source-of-truth skills (synced into adapters)
│   └── specs/loop-contract.md        # Canonical two-lane orchestration contract
├── platforms/
│   ├── antigravity/                  # Phase 1 — native Antigravity plugin + build_plan.py workflow
│   └── claude/                       # Phase 1 — native Claude Code plugin + lifecycle hooks
├── scripts/
│   ├── sync.py                       # Copies core skills and paddock_core/ into adapters (no symlinks)
│   └── install.py                    # Personal-machine install/uninstall helper
└── tests/                            # pytest suite (core, MCP, sync drift, Antigravity, Claude)
```

## Quick Start

- **Sync core skills & memory modules into platform adapters:**
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

## Environment Variables

- `PADDOCK_HOME`: Override unified memory store root (default: `~/.paddock`).
- `PADDOCK_SOLO=1`: Disable enforcement hooks for solo sessions.
