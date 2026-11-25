# Paddock CLI Packaging, GitHub Release Updater & README Overhaul Spec

Date: 2026-11-25
Status: Draft — Pending User Approval (Gate 1)
References: [paddock-spec.md](./paddock-spec.md), [agent-models.md](./agent-models.md), [releasing/SKILL.md](../../core/skills/releasing/SKILL.md)

---

## 1. Intent & Locked Decisions (from `/grill-me`)

Paddock currently requires contributors and end users alike to clone the repository manually, run `scripts/install.py` with `--repo` paths, manually pull updates from GitHub, and read developer commands (`sync.py`, `pytest`, Foreman migration) mixed into the top of `README.md`.

With `vdtongdev/paddock` now **public** on GitHub, this specification standardizes **minimal-step installation, updates, CI/release packaging, and documentation** across all platforms:

1. **Public GitHub repository (`https://github.com/vdtongdev/paddock`):** Enables zero-auth HTTPS `uv` / `uvx` installs, public GitHub Releases lookups, and direct Claude Code marketplace installs (`claude plugin marketplace add vdtongdev/paddock`).
2. **Zero-dependency `paddock` CLI (`pyproject.toml` + `uv tool install` / `uvx`):** Packages `core/`, `platforms/antigravity/`, `platforms/claude/`, and `.claude-plugin/marketplace.json` inside the wheel (`paddock/_bundle/`) with **zero third-party runtime dependencies** (`Python >= 3.12`) so users never need `git clone` on Windows, macOS, or Linux.
3. **Release-synced `paddock update`:** Syncs against the **latest GitHub Release tag (`vX.Y.Z`)** by default, upgrading both the `paddock` CLI tool and all detected installed plugins in one command (`--check`, `--version vX.Y.Z`, `--main`, `--platform`, `--plugins-only`, `--dry-run`, `--json`).
4. **Auto-detecting, hands-off `paddock install`:** With no platform argument, inspects the local environment (`~/.gemini` -> `antigravity`, `claude` CLI on `PATH` or `~/.claude` -> `claude`) and executes Claude's native marketplace commands (`claude plugin marketplace add` + `claude plugin install paddock@paddock`) automatically, falling back to printing copy-paste commands only if `claude` is missing or `--dry-run` is passed. Explicit `antigravity | claude | all` targets remain supported.
5. **Unified CLI surface:** Exposes `paddock install`, `paddock update`, `paddock uninstall`, `paddock status`, `paddock models ...`, and `paddock --version` (keeping `core/memory/memory.py` MCP/internal-only, and keeping `scripts/install.py` and `scripts/models.py` as thin backward-compatible wrappers).
6. **GitHub Actions CI & Release automation:** Adds `.github/workflows/ci.yml` (cross-platform Windows + Ubuntu sync check, version parity, `pytest`, wheel build + out-of-repo smoke test) and `.github/workflows/release.yml` (on `v*` tags: tag/version parity check, full verification, deterministic plugin zips + `SHA256SUMS.txt`, GitHub Release upload).
7. **User-first `README.md`:** Separates end-user **Install / Update / Uninstall**, **Model Configuration**, and contributor **Development**.

---

## 2. Packaging & Wheel Bundle Layout (`pyproject.toml` + `paddock/`)

### 2.1 Package Metadata (`pyproject.toml`)

```toml
[build-system]
requires = ["hatchling>=1.25"]
build-backend = "hatchling.build"

[project]
name = "paddock"
version = "0.2.0"
description = "Multi-platform orchestration loop and unified ~/.paddock/ typed memory suite"
readme = "README.md"
requires-python = ">=3.12"
dependencies = []

[project.scripts]
paddock = "paddock.cli:main"

[tool.hatch.build.targets.wheel]
packages = ["paddock", "core"]

[tool.hatch.build.targets.wheel.force-include]
"core" = "paddock/_bundle/core"
"platforms/antigravity" = "paddock/_bundle/platforms/antigravity"
"platforms/claude" = "paddock/_bundle/platforms/claude"
".claude-plugin/marketplace.json" = "paddock/_bundle/.claude-plugin/marketplace.json"
```

Excludes `__pycache__`, `*.pyc`, `*.pyo`, and `.pytest_cache` from wheel builds (`[tool.hatch.build.targets.wheel]` `exclude` patterns). Runtime code remains 100% Python 3.12 stdlib with `dependencies = []`.

### 2.2 Bundle Layout & Source-Root Resolution (`paddock.installer.resolve_source_root`)

`paddock/` package modules (`__init__.py`, `cli.py`, `installer.py`, `updater.py`) resolve assets without assuming a git checkout:

1. **Explicit override (`--repo <dir>`):** Uses resolved `<dir>` if provided (validating directory exists).
2. **Local repo checkout (e.g., `scripts/install.py` inside a clone):** Uses repo root when `(repo_root / "scripts" / "sync.py").is_file()` and `platforms/antigravity` exist.
3. **Packaged wheel bundle (`paddock/_bundle/`):** Falls back to `Path(__file__).resolve().parent / "_bundle"` when invoked via `uv tool install`, `uvx`, or `pip` outside a repo clone.

**Drift & Integrity Check Behavior:**
- When `source_root` is a repository checkout (`(source_root / "scripts" / "sync.py").is_file()`), `install_antigravity` runs `sync.sync_all(source_root, check=True)` and aborts with a clear message if core/platform sync drift exists.
- When `source_root` is a built wheel `_bundle/`, `install_antigravity` skips `sync.py` and instead verifies required pre-synced manifests and assets exist (`platforms/antigravity/plugin.json`, `platforms/antigravity/scripts/paddock_core/{memory.py,mcp_server.py,models.py,agents.py}`, `core/agents/*.md`, `core/skills/paddock-models/SKILL.md`).

### 2.3 Single-Source Version Parity (`check_version_parity`)

`scripts/sync.py` adds `check_version_parity(repo_root: Path) -> list[str]` (invoked inside `sync_all(repo_root, check=...)` and tested in `tests/test_sync_drift.py`), verifying one identical semantic version across:
- `pyproject.toml` (`[project] version`)
- `paddock/__init__.py` (`__version__`)
- `platforms/antigravity/plugin.json` (`version`)
- `platforms/claude/.claude-plugin/plugin.json` (`version`)
- `core/memory/mcp_server.py` (`SERVER_INFO["version"]`)
- Synced `platforms/antigravity/scripts/paddock_core/mcp_server.py` and `platforms/claude/scripts/paddock_core/mcp_server.py`

Any mismatch is reported cleanly by `python scripts/sync.py --check` (`exit 1`) and CI.

---

## 3. Unified `paddock` CLI (`paddock/cli.py`, `paddock/installer.py`, `paddock/updater.py`)

`paddock` exposes a clean top-level CLI while keeping `scripts/install.py` and `scripts/models.py` working identically as thin backward-compatible wrappers.

### 3.1 Commands & Flags

| Command | Purpose & Behavior | Key Flags |
| :--- | :--- | :--- |
| `paddock --version` | Print `paddock <__version__>` and exit `0`. | — |
| `paddock install [target]` | Positional `target` (`antigravity`, `claude`, `all`) or omitted for auto-detect (`~/.gemini` -> `antigravity`, `claude` on `PATH` or `~/.claude` -> `claude`; if neither detected, print guidance telling user which target exists/how to pass `antigravity` or `claude`). Executes `install_antigravity` in-place (preserving locked root dirs, pruning stale files, applying global `~/.paddock/models.json` profiles) and for Claude runs `claude plugin marketplace add vdtongdev/paddock` (or `marketplace update` if already registered) + `claude plugin install paddock@paddock`, then applies static agent profiles (`--dry-run` or missing `claude` CLI prints exact commands cleanly). | `--platform <antigravity\|claude\|all>`, `--dry-run`, `--uninstall`, `--target <dir>`, `--home <dir>`, `--repo <dir>`, `--json` |
| `paddock uninstall [target]` | Auto-detect or target `antigravity \| claude \| all`. Removes `~/.gemini/config/plugins/paddock` and/or runs `claude plugin uninstall paddock@paddock` (printing command if `claude` is absent). **Never** deletes `~/.paddock/` user memory or `models.json`. | `--platform <antigravity\|claude\|all>`, `--dry-run`, `--home <dir>`, `--repo <dir>`, `--json` |
| `paddock update` | Syncs CLI and installed plugins to latest GitHub Release tag (`vX.Y.Z`) by default. | `--check`, `--version <vX.Y.Z>`, `--main`, `--platform <antigravity\|claude\|all>`, `--plugins-only`, `--dry-run`, `--home <dir>`, `--json` |
| `paddock status` | Shows CLI version, source mode (`repo` vs `bundle`), installed platform plugin paths & versions, active global/project `models.json` preset/slug, and tool availability (`uv`, `claude`, `git`, `gh`). | `--cwd <path>`, `--home <dir>`, `--json` |
| `paddock models ...` | Forwards remaining argv verbatim to `core.config.models.main(rest)`. | All `models.py` flags |

### 3.2 `paddock update` Execution Flow & Windows Lock Handling

1. **Resolve target ref:**
   - Default: query `https://api.github.com/repos/vdtongdev/paddock/releases/latest` via stdlib `urllib.request` (adding `Authorization: Bearer ...` only if `GITHUB_TOKEN` or `GH_TOKEN` is non-empty, never echoing headers/tokens). On HTTP error / rate limit, try `gh api repos/vdtongdev/paddock/releases/latest` as a fallback if `gh` is installed; fail cleanly with actionable diagnostics if offline.
   - `--version <tag>`: validate strictly against `^v\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$`.
   - `--main`: target git ref `main`.
   - Compare latest tag against current `paddock.__version__` (`--check` outputs status/available update JSON or text and exits `0` cleanly).
2. **Upgrade CLI tool (unless `--plugins-only` or `--dry-run`):**
   - Check `uv` on `PATH`; run `["uv", "tool", "install", "--force", f"git+https://github.com/vdtongdev/paddock.git@{ref}"]`.
   - **Windows self-update lock handling:** On Windows, overwriting a running `paddock.exe` binary inside `uv tool install --force` can raise an access/file-in-use error (`WinError 5` / `WinError 32` / `being used by another process`). Detect this condition cleanly, emit a structured non-destructive next-step instruction:
     ```text
     uv tool install --force git+https://github.com/vdtongdev/paddock.git@<ref>
     paddock install
     ```
     and exit `1` without touching or partially modifying installed plugins.
3. **Sync installed platform plugins:**
   - When running via the upgraded tool (or `--plugins-only`), execute `paddock install` for detected/selected installed platforms (`antigravity` in-place sync + `claude plugin marketplace update paddock` / `claude plugin update paddock@paddock` + `models apply`) so plugins match the new release cleanly.

---

## 4. GitHub Actions CI & Release Workflows (`.github/workflows/`)

### 4.1 `.github/workflows/ci.yml`

- **Triggers:** `push` on `main`, `pull_request` targeting `main`.
- **Permissions:** `contents: read`.
- **Matrix:** `ubuntu-latest` and `windows-latest`, Python `3.12` + `uv` (`astral-sh/setup-uv`).
- **Steps:**
  1. `python scripts/sync.py --check` (verifies skills, `paddock_core/`, compiled agents, and version parity across all manifests).
  2. `python -m pytest -q`.
  3. Build wheel (`uv build --wheel --out-dir dist`) and install into an isolated venv or `uv tool` / `uvx --from dist/*.whl` outside the workspace directory, running `paddock --version` and `paddock status --json` to verify bundled `_bundle/` execution without a repo checkout.

### 4.2 `.github/workflows/release.yml`

- **Triggers:** `push` matching tag `v*`.
- **Permissions:** `contents: write`.
- **Steps (on `ubuntu-latest`, Python `3.12` + `uv`):**
  1. Assert `GITHUB_REF_NAME` matches `v<pyproject.toml version>` (`^v\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$`).
  2. Run `python scripts/sync.py --check` and `python -m pytest -q`.
  3. Build deterministic plugin archives (fixed DOS/ZIP timestamps or sorted entries, excluding `__pycache__`, `*.pyc`, `*.pyo`, `.pytest_cache`):
     - `paddock-antigravity-<tag>.zip` (from `platforms/antigravity/`)
     - `paddock-claude-<tag>.zip` (from `platforms/claude/`)
     - `SHA256SUMS.txt` (over zips + built wheel if desired)
  4. Publish/update GitHub Release `<tag>`: if release `<tag>` already exists, run `gh release upload <tag> ... --clobber`; otherwise create it with `gh release create <tag> --verify-tag --generate-notes` and attach the zips + `SHA256SUMS.txt`.

---

## 5. `README.md` Restructure

Rewrite `README.md` with clear separation between end-user onboarding and contributor workflows:

1. **`## Install`:**
   - **Prerequisites:** Python `3.12+` and [`uv`](https://docs.astral.sh/uv/).
   - **Recommended (All Platforms & Antigravity — 2 steps, no manual git clone):**
     ```bash
     uv tool install git+https://github.com/vdtongdev/paddock.git
     paddock install
     ```
     Plus zero-install 1-liner option:
     ```bash
     uvx --from git+https://github.com/vdtongdev/paddock.git paddock install
     ```
   - **Claude Code Native Marketplace Alternative:**
     ```bash
     claude plugin marketplace add vdtongdev/paddock
     claude plugin install paddock@paddock
     ```
   - **`### Update`:** `paddock update` (and `paddock update --check` / `paddock update --version vX.Y.Z` / `paddock update --main` + Windows note if `paddock.exe` self-update lock occurs).
   - **`### Uninstall`:** `paddock uninstall` (notes `~/.paddock/` memory & model config are preserved).
2. **`## Model Configuration`:** Keep `/paddock-models`, `paddock models ...` CLI quick-start, multi-provider catalog table, Antigravity `swarm_model` vs `subagent_alias` summary, and global vs project precedence.
3. **`## Development`:** Move `git clone`, repository layout, `python scripts/sync.py --check`, and `python -m pytest -q` here for contributors only. *(Keep `import-claude` working in `core/memory/memory.py` and tests without cluttering `README.md` with legacy Foreman migration instructions.)*
4. **`## Environment Variables`:** `PADDOCK_HOME`, `PADDOCK_SOLO`.

---

## 6. Security

- **Hardcoded HTTPS repository:** All remote API and git fetches target `https://github.com/vdtongdev/paddock` only (no arbitrary user-supplied remote URLs in `paddock update`).
- **Strict ref & input validation:** `--version` rejects anything not matching `^v\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$`; platform targets strictly validated against allowed enums.
- **No `shell=True` & no secret leaks:** All `uv`, `claude`, and `gh` invocations use explicit argv lists without `shell=True`; `GITHUB_TOKEN`/`GH_TOKEN` headers are never logged or printed to stdout/stderr.
- **Path containment & symlink refusal:** `installer._copy_plugin_tree` refuses symlinked/junction roots and entries via `agents._is_symlink_or_junction`, writes files atomically via `sync._atomic_write_bytes`, never touches `~/.gemini/config/config.json`, and `uninstall` never removes `~/.paddock/`.
- **Least-privilege CI/Release permissions:** `ci.yml` uses `permissions: contents: read`; `release.yml` limits `contents: write` strictly to `v*` tag builds.

---

## 7. UI Design

N/A — CLI packaging, release automation, and documentation overhaul (interactive terminal TUI and Antigravity MCP App configurator are intentionally out of scope for this PR).

---

## 8. Verification & Testing (`python -m pytest -q`)

1. **`tests/test_paddock_cli.py` (new):**
   - `paddock --version` and `paddock status --json` (repo vs mocked `_bundle` mode).
   - `paddock install` auto-detection matrix (Antigravity-only, Claude-only, both detected, neither detected), live Claude CLI invocation args vs print fallback when `claude` CLI missing or `--dry-run`.
   - `paddock uninstall` preserving `~/.paddock/` memory and `models.json`.
   - `paddock models show --json` CLI delegation (and verify `memory` is not exposed as a public CLI subcommand).
   - Wheel `_bundle/` fallback installation outside the git repository (`install_antigravity` without `scripts/sync.py` present, still enforcing required asset checks).
   - `paddock update`: `--check`, latest release tag resolution via mocked `urllib.request` and `gh` fallback, `--version` SemVer validation & injection rejection, `--main`, `--dry-run`, `--plugins-only`, and graceful Windows self-update lock error message (`WinError 5`/`32`).
   - Backward compatibility of `scripts/install.py` and `scripts/models.py` wrappers.
2. **`tests/test_sync_drift.py` (extended):**
   - `check_version_parity` catches intentional version mismatches across `pyproject.toml`, `paddock/__init__.py`, `plugin.json` × 2, and `mcp_server.py` × 3.
   - Structural validation of `.github/workflows/ci.yml` and `.github/workflows/release.yml` (permissions, triggers, `sync.py --check`, `pytest`).
   - All existing 58 tests continue passing cleanly on Windows.
