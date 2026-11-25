# Implementation Plan: Paddock CLI Packaging, GitHub Release Updater & README Overhaul

Date: 2026-11-25
Spec: [docs/specs/release-installer.md](../specs/release-installer.md)
Target Branch: `feat/release-installer` (worktree `.worktrees/release-installer` off `main`)

---

## Execution Rules & Hard Constraints

- **Zero third-party runtime dependencies:** `pyproject.toml` must keep `requires-python = ">=3.12"` and `dependencies = []`. All code in `paddock/` and `core/` uses Python 3.12+ standard library only.
- **No `paddock memory` CLI subcommand:** Keep `core/memory/memory.py` MCP/internal-only. Do not register `memory` in `paddock/cli.py` or document it in `README.md`.
- **No legacy Foreman migration instructions in `README.md`:** Keep `import-claude` working in `core/memory/memory.py` and existing tests, without adding `~/.claude/` migration instructions to `README.md`.
- **Windows sandbox discipline:** Inside the Windows sandbox, run tests and sync checks strictly via `python` and `python -m pytest -q` (never `uv run ...`, which attempts external user cache/appdata access inside the sandbox). Commands that build or install wheels via `uv` (`uv build`, `uv venv` + `uv pip install`, or `uvx`) require `BypassSandbox: true` when fetching build backend `hatchling>=1.25` into the `uv` cache.
- **Parallelism map:** After **Task 0** and **Task 1**, **Task 2** (`installer.py` + wrappers) must complete before **Task 3** (`updater.py` + `cli.py`). **Task 4** (`.github/workflows/`) may execute in parallel with **Task 2**/**Task 3** once **Task 1** completes. **Task 5** (tests) runs after **Task 3** and **Task 4**. **Task 6** (`README.md` + `CHANGELOG.md`) may execute in parallel with **Task 4**/**Task 5** once CLI commands are finalized in **Task 3**. **Task 7** runs last as an independent `paddock-verifier` gate.

---

### Task 0 — Set Up Isolated Feature Worktree & Verify Baseline

- **Agent:** `paddock-implementer`
- **Files:** `.git/info/exclude`, `.worktrees/release-installer`, `docs/specs/release-installer.md`, `docs/plans/release-installer.md`
- **Scope:**
  - Execute in `Mode: git` from repository root `d:\Projects\paddock`:
    1. Verify `.worktrees/` is present in `.git/info/exclude` (never modify tracked `.gitignore`).
    2. Create isolated feature worktree `.worktrees/release-installer` on branch `feat/release-installer` from `main`:
       `git worktree add -b feat/release-installer .worktrees/release-installer main`
    3. Copy untracked `docs/specs/release-installer.md` and `docs/plans/release-installer.md` from main checkout into `.worktrees/release-installer/docs/` if absent in the new worktree.
    4. If `.codegraph/` exists in the repository root, run `codegraph init -y` inside `.worktrees/release-installer`; otherwise skip CodeGraph.
    5. Run baseline tests inside `.worktrees/release-installer` using `python` (never `uv run`) per Windows sandbox rules.
- **Acceptance:**
  - `git worktree list` shows `.worktrees/release-installer` checked out on `feat/release-installer`.
  - `git -C .worktrees/release-installer diff --quiet main` exits `0` (tracked files untouched) and `.gitignore` is unmodified.
  - `python -m pytest -q` inside `.worktrees/release-installer` passes all 58 existing tests (`exit 0`).

### Task 1 — Package Metadata, Bundle Layout & `check_version_parity`

- **Agent:** `paddock-implementer`
- **Files:** `pyproject.toml`, `core/__init__.py`, `core/memory/__init__.py`, `paddock/__init__.py`, `paddock/version.py`, `scripts/sync.py`
- **Scope:**
  - Create `pyproject.toml` matching spec Section 2.1:
    - `[build-system]`: `requires = ["hatchling>=1.25"]`, `build-backend = "hatchling.build"`.
    - `[project]`: `name = "paddock"`, `version = "0.2.0"`, `description = "Multi-platform orchestration loop and unified ~/.paddock/ typed memory suite"`, `readme = "README.md"`, `requires-python = ">=3.12"`, `dependencies = []`.
    - `[project.scripts]`: `paddock = "paddock.cli:main"`.
    - `[tool.hatch.build.targets.wheel]`: `packages = ["paddock", "core"]` with `exclude` patterns stripping `**/__pycache__`, `**/*.pyc`, `**/*.pyo`, `**/.pytest_cache` from both packages and `force-include` entries.
    - `[tool.hatch.build.targets.wheel.force-include]`: map `"core"` -> `"paddock/_bundle/core"`, `"platforms/antigravity"` -> `"paddock/_bundle/platforms/antigravity"`, `"platforms/claude"` -> `"paddock/_bundle/platforms/claude"`, `".claude-plugin/marketplace.json"` -> `"paddock/_bundle/.claude-plugin/marketplace.json"` (populated into `paddock/_bundle/` inside the wheel from the `scripts/sync.py`-synced tree without committing a duplicate `_bundle/` directory to git).
  - Add minimal package docstring markers in `core/__init__.py` and `core/memory/__init__.py` so `core`, `core.config`, and `core.memory` resolve as standard packages inside wheel builds without affecting `scripts/sync.py` file lists (`CORE_MEMORY_FILES`, `CORE_CONFIG_FILES`).
  - Create `paddock/__init__.py` defining `__version__ = "0.2.0"` directly (matching spec Section 2.3 and `check_version_parity`).
  - Create `paddock/version.py` implementing stdlib SemVer validation and comparison helpers: `SEMVER_TAG_RE` (`^v\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$`), `validate_release_tag(tag: str) -> str`, `parse_semver(raw: str) -> tuple[tuple[int, int, int], tuple[str | int, ...]]`, `compare_versions(a: str, b: str) -> int`, and `is_newer_version(candidate: str, current: str) -> bool`.
  - Extend `scripts/sync.py` with `check_version_parity(repo_root: Path) -> list[str]` checking one identical semantic version across all 7 locations:
    1. `pyproject.toml` (`[project] version` parsed with stdlib `tomllib`)
    2. `paddock/__init__.py` (`__version__`)
    3. `platforms/antigravity/plugin.json` (`version`)
    4. `platforms/claude/.claude-plugin/plugin.json` (`version`)
    5. `core/memory/mcp_server.py` (`SERVER_INFO["version"]`)
    6. `platforms/antigravity/scripts/paddock_core/mcp_server.py` (`SERVER_INFO["version"]`)
    7. `platforms/claude/scripts/paddock_core/mcp_server.py` (`SERVER_INFO["version"]`)
    Report missing/unparseable/mismatched files clearly. Call `check_version_parity(repo_root)` inside `sync_all(repo_root, check=...)` whenever `check=True` and `(repo_root / "pyproject.toml").is_file() or (repo_root / "paddock" / "__init__.py").is_file()` (so existing minimal `_make_sync_repo` fixtures in `tests/test_sync_drift.py` continue passing unaffected), and verify parity after `sync_all(..., check=False)` in `main()` so `python scripts/sync.py` exits `1` if manual manifests still disagree.
- **Acceptance:**
  - `python scripts/sync.py --check` prints `OK: zero sync drift` and exits `0`.
  - `python -c "import tomllib, paddock, paddock.version; assert tomllib.load(open('pyproject.toml','rb'))['project']['version'] == paddock.__version__ == '0.2.0'; assert paddock.version.is_newer_version('v0.3.0', '0.2.0')"` exits `0`.
  - `python -m pytest -q tests/test_sync_drift.py` passes all existing tests (`exit 0`).

### Task 2 — Installer Engine (`paddock/installer.py`), Status Helper & Thin Script Wrappers

- **Agent:** `paddock-implementer`
- **Files:** `paddock/installer.py`, `scripts/install.py`, `scripts/models.py`
- **Scope:**
  - Implement `paddock/installer.py` using stdlib + `core.config.{agents,models}` and `core.memory.memory`:
    - `resolve_source_root(repo: str | Path | None = None) -> tuple[Path, str]` (and/or `resolve_source_root(repo=None) -> Path` + `source_mode(root: Path) -> str` helper returning `"repo"` or `"bundle"`):
      1. Explicit `repo`: validate non-traversal, non-symlink/junction, and `is_dir()`.
      2. Local repo checkout: inspect `Path(__file__).resolve().parent.parent`; treat as `"repo"` when `(root / "scripts" / "sync.py").is_file()` and `(root / "platforms" / "antigravity").is_dir()`.
      3. Packaged wheel bundle fallback: `Path(__file__).resolve().parent / "_bundle"` when `is_dir()`, returning mode `"bundle"`.
    - `verify_bundle_integrity(bundle_root: Path) -> None`: validate pre-synced required assets (`platforms/antigravity/plugin.json`, `platforms/claude/.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`, `platforms/antigravity/scripts/paddock_core/{memory.py,mcp_server.py,models.py,agents.py}`, `platforms/claude/scripts/paddock_core/{memory.py,mcp_server.py,models.py,agents.py}`, `core/agents/*.md`, `core/skills/paddock-models/SKILL.md`).
    - Implement `_atomic_write_bytes` locally (using closed `tempfile.mkstemp` + `os.replace`) so wheel `_bundle/` installs never import `scripts/sync.py`; when in `"repo"` mode, load `source_root / "scripts" / "sync.py"` dynamically via `importlib.util` and run `sync_all(source_root, check=True)` before Antigravity installation, aborting with clear message on drift.
    - Keep `_copy_plugin_tree` symlink/junction guards via `agents._is_symlink_or_junction`, in-place atomic updates, stale pruning, and never touch `~/.gemini/config/config.json`.
    - `detect_platforms(home_dir: Path) -> list[str]`: check `(home_dir / ".gemini").is_dir()` -> `"antigravity"`; `shutil.which("claude") is not None or (home_dir / ".claude").is_dir()` -> `"claude"`.
    - `install_antigravity` & `install_claude`:
      - Preserve `install_antigravity(repo_root, home_dir=None, *, target=None, dry_run=False, uninstall=False, paddock_home=None) -> list[str]` signature.
      - Update `install_claude(repo_root, home_dir=None, *, target=None, dry_run=False, uninstall=False, paddock_home=None, marketplace_source=None, update_existing=False) -> list[str]`:
        - Marketplace ref defaults to `"vdtongdev/paddock"` (or `str(repo_root)` when `--repo` override is explicitly supplied).
        - When `uninstall=True`: verify `.claude-plugin/marketplace.json`; if `claude` is on `PATH` and `not dry_run`, run `[claude_bin, "plugin", "uninstall", "paddock@paddock"]` (`shell=False`, isolated `HOME`/`USERPROFILE` when `home_dir` differs from `Path.home()`); otherwise return fallback terminal command lines. Never delete `~/.paddock/`.
        - When `target` is passed: run `agents.apply_models_to_targets(platform="claude", ...)` directly.
        - When installing (`uninstall=False`, `target=None`): if `claude` is on `PATH` and `not dry_run`, execute `[claude_bin, "plugin", "marketplace", "add", marketplace_ref]` (falling back to `[claude_bin, "plugin", "marketplace", "update", "paddock"]` if already registered) and `[claude_bin, "plugin", "install", "paddock@paddock"]` (or `["plugin", "update", "paddock@paddock"]` when `update_existing=True` or already installed), then run `agents.apply_models_to_targets(platform="claude", ..., source=repo_root)`. If `dry_run` or `claude` CLI is missing, print exact copy-paste commands cleanly (still applying `models.json` if target plugins already exist on disk and `not dry_run`).
    - Implement `install_targets(...)`, `uninstall_targets(...)`, and `collect_status(cwd: str | Path = ".", home_dir: Path | None = None, paddock_home: Path | str | None = None) -> dict[str, Any]` (reporting CLI `__version__`, `source_mode`, `source_root`, Antigravity & Claude installed paths + plugin versions, active global/project `models.json` preset/slug/paths, and `uv`/`claude`/`git`/`gh` availability).
  - Convert `scripts/install.py` and `scripts/models.py` into thin backward-compatible wrappers delegating to `paddock.installer` and `core.config.models.main`, preserving `scripts.install` attributes (`install_antigravity`, `install_claude`, `main`, `shutil`).
- **Acceptance:**
  - `python scripts/install.py --platform all --dry-run` exits `0`.
  - `python scripts/models.py show --json` exits `0` with valid JSON.
  - `python -m pytest -q tests/test_models_config.py tests/test_agents_standard.py` passes (`exit 0`).

### Task 3 — Release Updater (`paddock/updater.py`) & Unified CLI (`paddock/cli.py`)

- **Agent:** `paddock-implementer`
- **Files:** `paddock/updater.py`, `paddock/cli.py`
- **Scope:**
  - Implement `paddock/updater.py`:
    - Hardcode remote constants `GITHUB_REPO = "vdtongdev/paddock"`, `GITHUB_REPO_URL = "https://github.com/vdtongdev/paddock.git"`, `GITHUB_LATEST_RELEASE_API = "https://api.github.com/repos/vdtongdev/paddock/releases/latest"`. Never accept arbitrary remote URLs.
    - `resolve_target_ref(*, version: str | None = None, use_main: bool = False) -> dict[str, Any]`:
      - Reject combining `--version` and `--main`.
      - Validate `--version` strictly via `paddock.version.validate_release_tag` (`^v\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$`).
      - `--main` resolves to `ref = "main"`.
      - Default queries `GITHUB_LATEST_RELEASE_API` via stdlib `urllib.request.Request` (adding `Authorization: Bearer ...` only if `GITHUB_TOKEN` or `GH_TOKEN` is non-empty, never printing/logging headers or tokens in output/exceptions), falling back on HTTP/network error to `["gh", "api", "repos/vdtongdev/paddock/releases/latest"]` (`shell=False`) when `gh` is on `PATH`, and failing with clear actionable offline diagnostics if both fail.
    - `run_update(...)`:
      - Supports `--check`, `--version`, `--main`, `--platform <antigravity|claude|all>`, `--plugins-only`, `--dry-run`, `--home`, `--json`.
      - `--check`: compares resolved release tag against `paddock.__version__` using `paddock.version.is_newer_version`, returns structured status/text and exits `0`.
      - Unless `--plugins-only` or `--dry-run`: locate `uv` via `shutil.which("uv")`, run `["uv", "tool", "install", "--force", f"{GITHUB_REPO_URL}@{ref}"]` (`shell=False`). Detect Windows file-in-use/access errors (`WinError 5`, `WinError 32`, `os error 5`, `os error 32`, `being used by another process`, `access is denied`) cleanly, avoid touching installed plugins, emit exact two-line recovery instructions:
        `uv tool install --force git+https://github.com/vdtongdev/paddock.git@<ref>`
        `paddock install`
        and return exit `1`.
      - After tool upgrade succeeds, invoke upgraded `paddock` binary (`install` subcommand with selected platform/`--home`/`--json` and update mode) so plugins install from the new release's `_bundle/`; when `--plugins-only` is passed, execute in-process via `paddock.installer` (`update_existing=True`).
  - Implement `paddock/cli.py`:
    - `main(argv: list[str] | None = None) -> int`:
      - Intercept `argv` starting with `"models"` before subparser parsing so `paddock models ...` forwards remaining args verbatim to `core.config.models.main(rest)` (including `--help` and `--json`).
      - Expose `--version` (`paddock <__version__>`, exit `0`), `install`, `uninstall`, `update`, `status`, and `models` (documented in `--help` epilog/subcommand list).
      - Strictly do **not** expose `memory` subcommand.
      - Handle `install [target]` / `--platform` (`antigravity | claude | all` or auto-detect, printing guidance if neither `~/.gemini` nor `claude`/`~/.claude` is found), `--dry-run`, `--uninstall`, `--target`, `--home`, `--repo`, `--json`.
      - Handle `uninstall [target]` / `--platform`, `--dry-run`, `--home`, `--repo`, `--json` (preserving `~/.paddock/`).
      - Handle `status` (`--cwd`, `--home`, `--json`).
- **Acceptance:**
  - `python -m paddock.cli --version` prints `paddock 0.2.0` and exits `0`.
  - `python -m paddock.cli status --json` prints valid JSON containing `"version": "0.2.0"` and `"source_mode": "repo"` (`exit 0`).
  - `python -m paddock.cli models show --json` exits `0`.
  - `python -m paddock.cli memory` exits non-zero (rejected as invalid command).
  - `python -m paddock.cli update --version "bad;tag" --dry-run` exits non-zero without executing `uv`.

### Task 4 — GitHub Actions CI & Release Workflows

- **Agent:** `paddock-implementer`
- **Files:** `.github/workflows/ci.yml`, `.github/workflows/release.yml`
- **Scope:**
  - Create `.github/workflows/ci.yml`:
    - Triggers: `push` on `main`, `pull_request` targeting `main`.
    - `permissions: contents: read`.
    - Matrix: `os: [ubuntu-latest, windows-latest]`, `fail-fast: false`.
    - Steps: `actions/checkout@v4`, `actions/setup-python@v5` (`python-version: "3.12"`), `astral-sh/setup-uv@v5`, `python scripts/sync.py --check`, `python -m pytest -q`, `uv build --wheel --out-dir dist`, cross-platform Python wheel verification & out-of-repo smoke check (assert `dist/*.whl` contains `paddock/_bundle/` and zero `__pycache__`/`*.pyc`/`*.pyo`/`.pytest_cache`, then create an isolated venv outside `${{ github.workspace }}` with `uv venv` + `uv pip install <wheel>` and execute `paddock --version`, `paddock status --json` asserting `source_mode == "bundle"`, and `paddock models show --json`).
  - Create `.github/workflows/release.yml`:
    - Triggers: `push` tags `["v*"]`.
    - `permissions: contents: write`.
    - Runs on `ubuntu-latest` with `actions/checkout@v4`, `actions/setup-python@v5` (`python-version: "3.12"`), `astral-sh/setup-uv@v5`.
    - Steps:
      1. Validate `GITHUB_REF_NAME` matches `^v\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$` and equals `v<pyproject.toml [project] version>`.
      2. Run `python scripts/sync.py --check` and `python -m pytest -q`.
      3. Build wheel via `uv build --wheel --out-dir dist`.
      4. Build deterministic plugin archives (`dist/paddock-antigravity-${GITHUB_REF_NAME}.zip` from `platforms/antigravity/` and `dist/paddock-claude-${GITHUB_REF_NAME}.zip` from `platforms/claude/`) using Python `zipfile` with sorted relative paths, fixed DOS timestamps `(2025, 1, 1, 0, 0, 0)`, normalized permissions, and excluding `__pycache__`, `*.pyc`, `*.pyo`, `.pytest_cache`; write `dist/SHA256SUMS.txt` covering the zips and wheel sorted by filename.
      5. Publish/update GitHub Release with `GH_TOKEN: ${{ github.token }}`: if `gh release view "$GITHUB_REF_NAME" >/dev/null 2>&1` succeeds, run `gh release upload "$GITHUB_REF_NAME" dist/*.zip dist/*.whl dist/SHA256SUMS.txt --clobber`; else run `gh release create "$GITHUB_REF_NAME" dist/*.zip dist/*.whl dist/SHA256SUMS.txt --verify-tag --generate-notes`.
- **Acceptance:**
  - Verify both files exist and pass structural validation via Python script checking required triggers, `permissions`, matrix OSes, `scripts/sync.py --check`, `pytest`, deterministic archive filters, and `gh release` commands (`exit 0`).

### Task 5 — CLI, Bundle, Updater, Version Parity & Workflow Tests

- **Agent:** `paddock-implementer`
- **Files:** `tests/test_paddock_cli.py`, `tests/test_sync_drift.py`
- **Scope:**
  - Create `tests/test_paddock_cli.py` testing every scenario from Spec Section 8.1:
    - `paddock --version` and `paddock status --json` (real `"repo"` mode and mocked `"bundle"` mode).
    - `paddock install` auto-detection matrix: Antigravity-only, Claude-only, both detected, neither detected (verifies guidance message and exit `0`).
    - Claude Code marketplace execution: mocks `shutil.which("claude")` and `subprocess.run` to assert `claude plugin marketplace add vdtongdev/paddock`, `marketplace update paddock` fallback when already registered, `claude plugin install paddock@paddock`, `shell=False`, and `models.json` application; verifies missing `claude` CLI and `--dry-run` fall back cleanly to printed instructions without invoking `subprocess.run`.
    - `paddock uninstall` (both platforms, dry-run and real) verifying `~/.paddock/memory` and `~/.paddock/models.json` remain completely intact.
    - `paddock models show --json` delegation and negative assertion that `paddock memory` fails and is absent from public CLI commands.
    - Wheel `_bundle/` fallback test: builds a temporary `_bundle/` directory outside any git repo (without `scripts/sync.py`), runs `install_antigravity`, and verifies `verify_bundle_integrity` catches missing required bundle files.
    - `paddock update`: mocked `urllib.request.urlopen` for `--check` and upgrade, `gh api` fallback when `urllib` fails, offline error sanitization (assert `GITHUB_TOKEN`/`GH_TOKEN` never appear in stdout/stderr), strict `--version` SemVer validation & injection rejection, `--main` + `--version` mutual exclusion, `--dry-run`, `--plugins-only`, and Windows self-update lock (`WinError 5` / `WinError 32`) leaving plugins untouched and printing the exact two recovery lines.
    - Backward compatibility of `scripts/install.py` and `scripts/models.py`.
  - Extend `tests/test_sync_drift.py`:
    - Add unit tests for `sync.check_version_parity` validating pass on repo root and failure on intentional mismatch across all 7 locations (`pyproject.toml`, `paddock/__init__.py`, `platforms/antigravity/plugin.json`, `platforms/claude/.claude-plugin/plugin.json`, `core/memory/mcp_server.py`, and both synced `paddock_core/mcp_server.py` copies).
    - Add structural validation tests for `.github/workflows/ci.yml` and `.github/workflows/release.yml` (using stdlib parsing without adding PyYAML).
    - In `test_install_claude_discovery_and_explicit_target_apply`, patch `shutil.which` to return `None` before checking fallback install instructions so tests never shell out to a host `claude` binary.
- **Acceptance:**
  - `python -m pytest -q` passes all existing (58) and new tests inside the worktree (`exit 0`, zero skips/failures).
  - `python scripts/sync.py --check` prints `OK: zero sync drift` and exits `0`.

### Task 6 — User-First `README.md` Overhaul & `CHANGELOG.md` Update

- **Agent:** `paddock-implementer`
- **Files:** `README.md`, `CHANGELOG.md`
- **Scope:**
  - Rewrite `README.md` strictly per Spec Section 5:
    - Lead with concise overview of Paddock (`Antigravity`, `Claude Code`, Phase 2 targets).
    - `## Install`: prerequisites (Python `3.12+` and `uv`), recommended 2-step zero-clone install (`uv tool install git+https://github.com/vdtongdev/paddock.git` + `paddock install`), 1-line `uvx --from git+https://github.com/vdtongdev/paddock.git paddock install`, Claude Code native marketplace alternative (`claude plugin marketplace add vdtongdev/paddock` + `claude plugin install paddock@paddock`), `### Update` (`paddock update`, `--check`, `--version vX.Y.Z`, `--main`, Windows self-update lock note), `### Uninstall` (`paddock uninstall`, noting `~/.paddock/` preservation), and `paddock status`.
    - `## Model Configuration`: `/paddock-models` chat command, `paddock models ...` CLI quick-start (`show`, `preset`, `set-provider`, `set-role`, `set-tier`, `set-override`, `apply`, `reset`), multi-provider tier catalog table, Antigravity `swarm_model` vs `subagent_alias` summary, and global vs per-project precedence.
    - `## Development`: contributor-only `git clone`, updated repo layout (including `pyproject.toml`, `paddock/`, `.github/workflows/`), `python scripts/sync.py --check`, `python -m pytest -q`, and contributor wrapper notes (`scripts/install.py`, `scripts/models.py`).
    - `## Environment Variables`: `PADDOCK_HOME` and `PADDOCK_SOLO`.
    - **Strict exclusions:** Must NOT contain legacy Foreman (`~/.claude/`) migration instructions (`import-claude`) or `paddock memory` CLI usage.
  - Update `CHANGELOG.md` under `## [Unreleased]` following Keep a Changelog 1.1.0 (`Added`, `Changed`, `Security`) covering packaging/bundle layout, zero-dependency `paddock` CLI (`install`, `uninstall`, `update`, `status`, `models`, `--version`), `check_version_parity`, GitHub Actions CI & Release workflows, CLI/drift tests, and user-first `README.md`.
- **Acceptance:**
  - Verify via Python check: `README.md` contains `uv tool install git+https://github.com/vdtongdev/paddock.git`, `paddock install`, `paddock update`, `paddock uninstall`, `paddock status`, `paddock models`, and `claude plugin marketplace add vdtongdev/paddock`; `README.md` contains zero occurrences of `import-claude`, `~/.claude/`, or `paddock memory`; `CHANGELOG.md` documents the `[Unreleased]` release-installer changes.

### Task 7 — Final Packaging Smoke Test & Independent `paddock-verifier` Gate

- **Agent:** `paddock-verifier`
- **Files:** `pyproject.toml`, `core/__init__.py`, `core/memory/__init__.py`, `paddock/__init__.py`, `paddock/version.py`, `paddock/installer.py`, `paddock/updater.py`, `paddock/cli.py`, `scripts/sync.py`, `scripts/install.py`, `scripts/models.py`, `.github/workflows/ci.yml`, `.github/workflows/release.yml`, `tests/test_paddock_cli.py`, `tests/test_sync_drift.py`, `README.md`, `CHANGELOG.md`
- **Scope:**
  - Read-only verification inside `.worktrees/release-installer` (never edit, create, or delete repository files):
    1. Verify git isolation (`.worktrees/release-installer` on `feat/release-installer`, `.worktrees/` in `.git/info/exclude`, tracked `.gitignore` unmodified, zero forbidden git actions).
    2. Verify spec invariants and negative constraints: zero runtime dependencies (`dependencies = []`, `requires-python = ">=3.12"`), no `paddock memory` subcommand, no legacy Foreman (`~/.claude/`) migration instructions in `README.md`.
    3. Run `python scripts/sync.py --check` and `python -m pytest -q` inside the worktree sandbox.
    4. Using an out-of-repo temporary directory (e.g., `%TEMP%\paddock-smoke-*`) and `BypassSandbox: true` (required on Windows for `uv` build/venv cache), run `uv build --wheel --out-dir <temp_dist>`, verify wheel contents via `python -m zipfile -l` (`paddock/_bundle/{core,platforms/antigravity,platforms/claude,.claude-plugin/marketplace.json}` present and zero `__pycache__`, `*.pyc`, `*.pyo`, `.pytest_cache`), install the wheel into `<temp_venv>` outside the repository (`uv venv` + `uv pip install`), and from `<temp_workdir>` execute `paddock --version`, `paddock status --json`, and `paddock models show --json`.
- **Acceptance:**
  - `python scripts/sync.py --check` prints `OK: zero sync drift` and exits `0`.
  - `python -m pytest -q` passes all tests with zero skips or failures (`exit 0`).
  - Built wheel passes `zipfile` bundle/cleanliness checks, and out-of-repo `paddock status --json` outputs `"version": "0.2.0"` and `"source_mode": "bundle"`.
  - Final verifier report includes `## Evidence`, `## Unverified`, and a single verdict line `PASS`.
