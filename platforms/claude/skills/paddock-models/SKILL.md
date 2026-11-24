---
name: paddock-models
description: Interactive multi-platform Paddock agent model configuration workflow via /paddock-models and models.py.
---

# Paddock Model Configuration (`/paddock-models`)

Configure Paddock's versioned multi-platform model settings (`~/.paddock/models.json` and per-project `~/.paddock/projects/<repo-slug>/models.json`) across `antigravity`, `claude`, `codex`, `copilot`, and `pi` without modifying target repositories.

## Locating the CLI

Resolve `models.py` in this order:
1. Installed plugin copy: `<plugin_root>/scripts/paddock_core/models.py` (for example `~/.gemini/config/plugins/paddock/scripts/paddock_core/models.py` on Antigravity, or the active Paddock plugin install path).
2. Paddock repository fallback: `scripts/models.py` (when working inside the Paddock repository).

Invoke commands with `uv run --no-project --script <models_py> ...` (or `python <models_py> ...` when `uv` is unavailable).

## Interactive Workflow

### 1. Inspect Current Effective Models

Run `show` for the current workspace first:

```bash
uv run --no-project --script <models_py> show --cwd <workspace>
```

- Pass `--platform <antigravity|claude|codex|copilot|pi>` to narrow output or `--json` for structured output.
- Present the observed active preset, global/project config status, and effective table (`model`, `effort`, and `source` layer: `project-override`, `project-tier`, `global-override`, `global-tier`, or `default`).
- On `antigravity`, `show` prints both `swarm_model` (`<model>-<effort>` for `swarm`) and `subagent_alias` (`flash`, `pro`, `inherit`, or `flash_lite` for `invoke_subagent` / `run_workflow`).

### 2. Prompt for Scope and Action

Ask the user interactively (using `ask_question` if available, otherwise a direct prompt) for:

1. **Scope:**
   - `global`: `~/.paddock/models.json` (shared default across repositories).
   - `project`: `~/.paddock/projects/<repo-slug>/models.json` (shared across main checkout and `.worktrees/*` for the current repository).
   - `all`: supported only by `reset` to clear both global and project configs.
2. **Action and arguments:**
   - `preset` — apply a built-in role tier/effort preset (`budget`, `balanced`, `quality`).
   - `set-provider` — switch one platform's tier catalog to a built-in provider:
     - `antigravity`: `google` (default), `anthropic`, `native`
     - `claude`: `anthropic` (default)
     - `codex`: `openai` (default)
     - `copilot`: `anthropic` (default), `openai`, `google`
     - `pi`: `anthropic` (default), `openai`, `google`
   - `set-role` — assign a role (`generalist`, `implementer`, `ui`, `planner`, `builder`, `verifier`) to an abstract tier (`fast`, `workhorse`, `flagship`) and optional `--effort <low|medium|high>` across all platforms.
   - `set-tier` — customize a concrete `--model <model>` and optional `--effort <low|medium|high>` for one `(platform, tier)` pair.
   - `set-override` — pin (`--model <model> [--effort <low|medium|high>]`) or clear (`--clear`) a per-platform per-role override.
   - `reset` — delete custom config for `global`, `project`, or `all` and restore `balanced` defaults.

Validation notes:
- Bare Antigravity aliases (`inherit`, `flash_lite`, `flash`, `pro`) do not accept `--effort`.
- `codex`, `copilot`, and `pi` accept bare IDs or `<provider>/<model-id>`; `antigravity` and `claude` accept single-segment model IDs (`^[a-z0-9][a-z0-9._-]{1,63}$`).

### 3. Execute the CLI Subcommand (and `apply` for Global Static Profiles)

Run the selected subcommand against `<workspace>`:

```bash
uv run --no-project --script <models_py> preset <budget|balanced|quality> --scope <global|project> --cwd <workspace>
uv run --no-project --script <models_py> set-provider <platform> <provider> --scope <global|project> --cwd <workspace>
uv run --no-project --script <models_py> set-role <role> <fast|workhorse|flagship> [--effort <low|medium|high>] --scope <global|project> --cwd <workspace>
uv run --no-project --script <models_py> set-tier <platform> <fast|workhorse|flagship> --model <model> [--effort <low|medium|high>] --scope <global|project> --cwd <workspace>
uv run --no-project --script <models_py> set-override <platform> <role> --model <model> [--effort <low|medium|high>] --scope <global|project> --cwd <workspace>
uv run --no-project --script <models_py> set-override <platform> <role> --clear --scope <global|project> --cwd <workspace>
uv run --no-project --script <models_py> reset --scope <global|project|all> --cwd <workspace>
```

When updating `global` settings (including `reset` with `--scope global` or `--scope all`) and installed Claude static agent profiles need to reflect the updated global config, run `apply`:

```bash
uv run --no-project --script <models_py> apply --platform claude --cwd <workspace>
```

- Use `--platform all` to update all installed static plugin agent targets, or `--dry-run` to preview changes first.
- `apply` renders installed static profiles from global `~/.paddock/models.json` only (project overrides are surfaced dynamically by hooks) and never modifies tracked files in the Paddock repository.

### 4. Refresh and Report Observed Output

Re-run `show` after the update (and after `apply`, when run):

```bash
uv run --no-project --script <models_py> show --cwd <workspace>
```

Report to the user:
- The exact CLI command(s) executed.
- The `apply` target summary if `apply` was run.
- The refreshed `show` output with exact observed models, efforts, aliases, and source layers.
