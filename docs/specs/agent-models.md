# Paddock Standard Agent Definitions & Model Configuration Spec

Date: 2026-11-24
Status: Approved (Gate 1)
References: [paddock-spec.md](./paddock-spec.md) (v2), [phase1-spike-results.md](./phase1-spike-results.md)

---

## 1. Intent & Two-Layer Multi-Platform Standard

Paddock supports multiple AI coding platforms (`antigravity`, `claude`, and Phase 2 targets `codex`, `copilot`, `pi`) that differ in how agent profiles and models are declared and dispatched (dynamic runtime arguments vs. static profile frontmatter vs. TOML/TS configs).

To standardize both **agent definitions** and **model selection** without duplicating prompts or polluting target repositories, Paddock defines two orthogonal layers:

1. **Layer 1 — Canonical Agent Definitions (`core/agents/<role>.md`):**
   - Single source of truth for all six Paddock roles (`generalist`, `implementer`, `ui`, `planner`, `builder`, `verifier`).
   - Uses platform-agnostic YAML frontmatter (`name`, `description`, `default_tier`, `platforms`, `capabilities`, `disallowed`) and a shared system prompt body.
   - Compiled into each platform's native agent format (`platforms/antigravity/agents/paddock-*.md`, `platforms/claude/agents/*.md`, and later Codex `.toml`, Copilot `.agent.md`, Pi) by `scripts/sync.py`, enforced by `tests/test_sync_drift.py`.
2. **Layer 2 — Versioned User Model Config (`~/.paddock/models.json`, `schema_version: 1`):**
   - Global store at `<PADDOCK_HOME>/models.json` plus optional per-project override at `<PADDOCK_HOME>/projects/<repo-slug>/models.json` (reusing `core/memory/memory.py`'s `repo_slug(cwd)` so main checkouts and `.worktrees/*` share config).
   - Survives plugin reinstalls/updates and never writes machine/user model preferences into target repositories.

---

## 2. Canonical Agent Schema & Capability Mapping (`core/agents/` + `core/config/agents.py`)

### 2.1 Canonical Frontmatter Schema (`core/agents/<role>.md`)

```yaml
---
name: implementer
description: Paddock implementation, bug diagnosis (Mode: diagnose), and git-write (Mode: git) specialist.
default_tier: workhorse
platforms: [antigravity, claude, codex, copilot, pi]
capabilities: [read, edit, write, shell, tasks, web]
disallowed: [delegate]
---
Shared role instructions...
```

### 2.2 Canonical Roles & Defaults

| Role | `default_tier` | `platforms` | `capabilities` | `disallowed` | Notes |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `generalist` | `fast` | `antigravity`, `claude`, `codex`, `copilot`, `pi` | `read`, `edit`, `write`, `search`, `web` | `shell`, `delegate` | No shell execution; read-only lookups & docs. |
| `implementer` | `workhorse` | `antigravity`, `claude`, `codex`, `copilot`, `pi` | `read`, `edit`, `write`, `shell`, `tasks`, `search`, `web` | `delegate` | Handles `Mode: diagnose`, `Mode: quick`, plan tasks, `Mode: git`. |
| `ui` | `workhorse` | `antigravity`, `claude`, `codex`, `copilot`, `pi` | `read`, `edit`, `write`, `shell`, `tasks`, `search`, `web` | `delegate` | Includes `impeccable` soft-dependency instructions (`npx impeccable detect --json`). |
| `planner` | `workhorse` | `antigravity`, `claude`, `codex`, `copilot`, `pi` | `read`, `write`, `edit`, `shell`, `search` | `delegate` | Decomposes approved specs into `Agent:`/`Files:`/`Scope:`/`Acceptance:` tasks. |
| `builder` | `workhorse` | `claude`, `codex`, `copilot`, `pi` | `delegate`, `read`, `search` | `edit`, `write`, `shell` | Omitted on `antigravity`, where deterministic `workflows/build_plan.py` replaces it. |
| `verifier` | `workhorse` | `antigravity`, `claude`, `codex`, `copilot`, `pi` | `read`, `shell`, `tasks`, `search` | `edit`, `write`, `delegate` | Strictly read-only; emits typed verdicts. |

### 2.3 Capability-to-Tool Mapping & Platform Output Footers

`core/config/agents.py` compiles canonical agents using deterministic per-platform capability tables:

| Capability | Antigravity (`paddock-<role>.md`) | Claude Code (`<role>.md`) |
| :--- | :--- | :--- |
| `read` | `read_file` | `Read` |
| `edit` | `edit_file` | `Edit` |
| `write` | `write_file` | `Write` |
| `shell` | `run_command` | `Bash` |
| `tasks` | `manage_task` | *(omit; native)* |
| `search` | *(implicit via `read_file`/`run_command`)* | `Grep`, `Glob` |
| `web` | `search_web` | `WebFetch`, `WebSearch` |
| `delegate` | *(not exposed to workers)* | `Agent` |

Platform rendering rules:
- **Antigravity (`platforms/antigravity/agents/paddock-<role>.md`):**
  - Frontmatter: `name: paddock-<role>`, `description`, `subagent: true`, `mainAgent: false`, `tools:` list (omitting `model:` because Antigravity passes `model` dynamically at dispatch time).
  - Appended output footer: standalone `invoke_subagent` replies require `## Evidence` and `## Unverified`; `run_workflow` invocations submit JSON via `finish` (for `verifier`, `VERDICT_SCHEMA`).
- **Claude Code (`platforms/claude/agents/<role>.md`):**
  - Frontmatter: `name: <role>`, `description`, `model: <resolved_model>`, `effort: <resolved_effort>`, comma-separated `tools:` and `disallowedTools:`.
  - Appended output footer: every reply ends with `## Evidence` and `## Unverified` (for `verifier`, plus single final verdict line `PASS | FAIL: impl | FAIL: plan | FAIL: spec | FAIL: env`).

### 2.4 Repository Sync Rule

`scripts/sync.py` always renders tracked repository files under `platforms/antigravity/agents/` and `platforms/claude/agents/` using **built-in `balanced` defaults** so personal `~/.paddock/models.json` edits never dirty git working trees. It also syncs:
- `core/agents/*.md` into `platforms/{antigravity,claude}/scripts/paddock_core/agents/` so installed `models.py apply` works standalone outside the Paddock repository.
- `core/config/models.py` and `core/config/agents.py` into `platforms/{antigravity,claude}/scripts/paddock_core/`.
- `core/skills/paddock-models/SKILL.md` into `platforms/{antigravity,claude}/skills/paddock-models/`.

---

## 3. Versioned Model Config Schema (`schema_version: 1`) & Precedence (`core/config/models.py`)

### 3.1 File Format (`~/.paddock/models.json` and `~/.paddock/projects/<slug>/models.json`)

```json
{
  "schema_version": 1,
  "preset": "balanced",
  "tiers": {
    "antigravity": {
      "fast": {"model": "gemini-4-hydrogen", "effort": "medium"},
      "workhorse": {"model": "gemini-4-neon", "effort": "medium"},
      "flagship": {"model": "gemini-4-carbon", "effort": "medium"}
    },
    "claude": {
      "fast": {"model": "haiku", "effort": "medium"},
      "workhorse": {"model": "sonnet", "effort": "medium"},
      "flagship": {"model": "opus", "effort": "medium"}
    },
    "codex": {
      "fast": {"model": "gpt-6-luna", "effort": "medium"},
      "workhorse": {"model": "gpt-6.2-sol", "effort": "medium"},
      "flagship": {"model": "gpt-6.2-astra", "effort": "medium"}
    },
    "copilot": {
      "fast": {"model": "claude-haiku-5.5", "effort": "medium"},
      "workhorse": {"model": "claude-sonnet-5.5", "effort": "medium"},
      "flagship": {"model": "claude-opus-5.5", "effort": "medium"}
    },
    "pi": {
      "fast": {"model": "anthropic/claude-haiku-5-5", "effort": "medium"},
      "workhorse": {"model": "anthropic/claude-sonnet-5-5", "effort": "medium"},
      "flagship": {"model": "anthropic/claude-opus-5-5", "effort": "medium"}
    }
  },
  "roles": {
    "generalist": "fast",
    "implementer": "workhorse",
    "ui": "workhorse",
    "planner": "workhorse",
    "builder": "workhorse",
    "verifier": "workhorse"
  },
  "overrides": {
    "antigravity": {},
    "claude": {}
  }
}
```

Each `roles.<role>` value may be either a tier string (`"fast" | "workhorse" | "flagship"`, inheriting the tier's default `"medium"` effort) or an object `{"tier": "fast" | "workhorse" | "flagship", "effort": "low" | "medium" | "high"}` (applied across all five platforms `antigravity`, `claude`, `codex`, `copilot`, and `pi` — formatted as `<model>-<effort>` on Antigravity `swarm`, and ignored cleanly only when a role resolves to a bare Antigravity runtime alias `inherit` / `flash_lite` / `flash` / `pro`).

Project config files may omit unmodified top-level keys (`tiers`, `roles`, `overrides`, `preset`); partial objects merge cleanly over the global config.

#### Built-in Provider Tier Catalogs

Presets project abstract tiers (`fast`, `workhorse`, `flagship`) onto each platform's active provider tier map. `antigravity` defaults to `google`, while `copilot` and `pi` default to `anthropic`; `set-provider` can switch multi-provider platforms between `google`, `anthropic`, `openai`, and (for Antigravity subagents) `native`:

| Platform / Provider | Effort Supported | `fast` | `workhorse` | `flagship` |
| :--- | :---: | :--- | :--- | :--- |
| `claude` (`anthropic`, default) | Yes (`effort`) | `haiku` | `sonnet` | `opus` |
| `codex` (`openai`, default) | Yes (`model_reasoning_effort`) | `gpt-6-luna` | `gpt-6.2-sol` | `gpt-6.2-astra` |
| `copilot` (`anthropic`, default) | Yes (`reasoning-effort` / `--effort`) | `claude-haiku-5.5` | `claude-sonnet-5.5` | `claude-opus-5.5` |
| `copilot` (`openai`) | Yes (`reasoning-effort` / `--effort`) | `gpt-6-luna` | `gpt-6.2-sol` | `gpt-6.2-astra` |
| `copilot` (`google`) | Yes (`reasoning-effort` / `--effort`) | `gemini-4-hydrogen` | `gemini-4-neon` | `gemini-4-carbon` |
| `pi` (`anthropic`, default) | Yes (`thinking`) | `anthropic/claude-haiku-5-5` | `anthropic/claude-sonnet-5-5` | `anthropic/claude-opus-5-5` |
| `pi` (`openai`) | Yes (`thinking`) | `openai/gpt-6-luna` | `openai/gpt-6.2-sol` | `openai/gpt-6.2-astra` |
| `pi` (`google`) | Yes (`thinking`) | `google/gemini-4-hydrogen` | `google/gemini-4-neon` | `google/gemini-4-carbon` |
| `antigravity` (`google`, default) | Yes (`<model>-<effort>` on `swarm`) | `gemini-4-hydrogen` | `gemini-4-neon` | `gemini-4-carbon` |
| `antigravity` (`anthropic`) | Yes (`<model>-<effort>` on `swarm`) | `gemini-4-hydrogen` | `claude-sonnet-5-5` | `claude-opus-5-5` |
| `antigravity` (`native`) | No (`invoke_subagent` / `run_workflow` enum) | `flash` | `pro` | `inherit` |

*(In `antigravity` (`anthropic`), `fast` uses hybrid `gemini-4-hydrogen` because Antigravity `swarm` does not expose Claude Haiku.)*

Antigravity `swarm` execution vs. `invoke_subagent` / `run_workflow` fallback:
- **`swarm` execution (exact concrete models):** `swarm(action="add", ...)` accepts exact `<model>-<effort>` IDs (`gemini-4-hydrogen-medium`, `gemini-4-hydrogen-high`, `gemini-4-neon-low`, `gemini-4-neon-medium`, `gemini-4-neon-high`, `gemini-4-carbon-medium`, `claude-sonnet-5-5-*`, `claude-opus-5-5-*`). With the default `google` catalog, presets resolve directly to:
  - `budget`: `generalist` = `gemini-4-hydrogen-medium`, `implementer`/`verifier` = `gemini-4-hydrogen-high`, `planner`/`ui`/`builder` = `gemini-4-neon-low`.
  - `balanced` *(default)*: `generalist` = `gemini-4-hydrogen-medium`, `implementer`/`verifier`/`planner`/`ui`/`builder` = `gemini-4-neon-medium` *(comparable to `claude-sonnet-5-5`: AA Intelligence Index 55 at Medium vs. Sonnet 5.5's 55–56 at Max)*.
  - `quality`: `generalist` = `gemini-4-hydrogen-medium`, `ui` = `gemini-4-neon-high`, `builder` = `gemini-4-neon-medium`, `implementer`/`planner`/`verifier` = `gemini-4-carbon-medium`.
  `agy_hooks.py` injects these exact `swarm_model` IDs via `PreToolUse` `overwrite` on `swarm` (`action == "add"`).
- **`invoke_subagent` / `run_workflow` (`build_plan.py`) fallback:** Antigravity's engine hard-rejects concrete model IDs outside the 4-value enum `inherit | flash_lite | flash | pro` (`WorkflowError`). `models.py` therefore computes `subagent_alias` alongside `swarm_model` via `antigravity_subagent_alias(...)` (`fast` -> `flash` [`gemini-4-hydrogen-tiered-high`], `workhorse` -> `pro` [`gemini-4-hydrogen-tiered-xhigh`], `flagship` -> `inherit`, or passing an explicit `inherit|flash_lite|flash|pro` model verbatim), and `models.py show --platform antigravity` prints both `swarm_model` and `subagent_alias`.

### 3.2 Built-in Presets

All tiers default to `medium` effort; presets rescale the provider-agnostic **abstract tier × effort** ladder by role fit:

| Preset | `generalist` | `implementer` | `ui` | `planner` | `builder` | `verifier` |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `budget` | `fast` (`medium`) | `fast` (`high`) | `workhorse` (`low`) | `workhorse` (`low`) | `workhorse` (`low`) | `fast` (`high`) |
| `balanced` *(default)* | `fast` (`medium`) | `workhorse` (`medium`) | `workhorse` (`medium`) | `workhorse` (`medium`) | `workhorse` (`medium`) | `workhorse` (`medium`) |
| `quality` | `fast` (`medium`) | `flagship` (`medium`) | `workhorse` (`high`) | `flagship` (`medium`) | `workhorse` (`medium`) | `flagship` (`medium`) |

Provider-agnostic role-fit rationale for `budget` (Anthropic `haiku`/`sonnet` is just one projection; switching providers via `set-provider` or `set-tier` re-projects every preset automatically onto the active provider's `fast` / `workhorse` / `flagship` models, such as `gpt-6-luna (high)` + `gpt-6.2-sol (low)` on OpenAI or `gemini-4-hydrogen (high)` + `gemini-4-neon (low)` on Google Gemini):
- **`planner`, `ui`, & `builder` -> `workhorse` (`low`):** Strategic task decomposition, multi-screen layout/visual polish, and multi-turn subagent orchestration depend on `workhorse`-class planning, design, and instruction-following priors where mistakes compound downstream, while `low` effort trims excess chain-of-thought tokens.
- **`implementer` & `verifier` -> `fast` (`high`):** Once `planner` produces tightly scoped tasks with exact `Files:`/`Scope:`/`Acceptance:`, `fast` models at `high` effort handle bounded code execution and strict rubric/test-output verification at much lower token cost (with `builder` or the orchestrator escalating to `workhorse` on repeated failures).
- **`generalist` -> `fast` (`medium`):** Lookups (`Mode: lookup`), summaries, and mechanical edits run cleanly on the `fast` tier at default `medium` effort without paying `high` thinking overhead.

### 3.3 Resolution Precedence (Highest to Lowest)

For any `(platform, role, cwd)` lookup:
1. **Call-time explicit argument** (e.g., non-default `Args.models` in `build_plan.py` or explicit non-`"inherit"` caller `Model` on `invoke_subagent`).
2. **Project platform override** (`project_cfg.overrides[platform][role]`).
3. **Project role tier** resolved via effective `tiers[platform][project_tier]` (where `project_tier` and optional role-level `effort` come from `project_cfg.roles[role]` or `project_cfg.preset`).
4. **Global platform override** (`global_cfg.overrides[platform][role]`).
5. **Global role tier** resolved via effective `tiers[platform][global_tier]` (where `global_tier` and optional role-level `effort` come from `global_cfg.roles[role]` or `global_cfg.preset`).
6. **Built-in `balanced` defaults**.

Role-level `effort` (from `roles.<role>.effort` or a preset) overrides the tier's default `"medium"` effort on effort-capable platforms, while platform-specific `overrides[platform][role]` overrides either or both `model` and `effort`.

### 3.4 Allowed Values & Validation

- **Supported platforms:** `antigravity`, `claude`, `codex`, `copilot`, `pi`.
- **Supported built-in providers:** `anthropic` (`claude`, `copilot`, `pi`, `antigravity`), `openai` (`codex`, `copilot`, `pi`), `google` (`antigravity`, `copilot`, `pi`), `native` (`antigravity`).
- **Supported roles:** `generalist`, `implementer`, `ui`, `planner`, `builder`, `verifier`.
- **Supported abstract tiers:** `fast`, `workhorse`, `flagship`.
- **Antigravity `model`:** accepts concrete model IDs matching `^[a-z0-9][a-z0-9._-]{1,63}$` (with optional `effort`: `low` | `medium` | `high` rendered as `<model>-<effort>` for `swarm`) OR bare runtime aliases `inherit`, `flash_lite`, `flash`, `pro` (no explicit `effort` allowed when setting a bare alias via `set-tier` / `set-override`; preset effort is ignored cleanly on bare aliases).
- **Claude `model`:** `haiku`, `sonnet`, `opus`, `inherit`, or concrete model ID matching `^[a-z0-9][a-z0-9._-]{1,63}$`.
- **`codex`, `copilot`, & `pi` `model`:** matches `^[a-z0-9][a-z0-9._-]{0,63}(?:/[a-z0-9][a-z0-9._-]{0,63})?$` (length 2–128, rejecting `".."`, leading/trailing `/`, multiple `/`, quotes, newlines, and YAML/shell metacharacters) so Pi's `<provider>/<model-id>` IDs validate cleanly.
- **Effort (`low`, `medium`, `high`):** supported on all five platforms (`antigravity` concrete models, `claude`, `codex`, `copilot`, and `pi`), defaulting to the resolved tier's effort or `"medium"`.

---

## 4. Command Interface (`/paddock-models` Skill + Python CLI)

### 4.1 Python CLI (`core/config/models.py` & `scripts/models.py`)

Both `uv run --no-project --script scripts/models.py ...` and `uv run --no-project --script platforms/<p>/scripts/paddock_core/models.py ...` expose identical subcommands:

- `show [--cwd <path>] [--platform <p>] [--json]` — Prints effective resolved models + source layer (`project-override`, `project-tier`, `global-override`, `global-tier`, `default`) per role and platform.
- `preset <budget|balanced|quality> [--scope global|project] [--cwd <path>]` — Applies a built-in preset (`roles` + `preset` field) atomically.
- `set-provider <platform> <provider> [--scope global|project] [--cwd <path>]` — Switches a platform's `tiers.<platform>` atomically to a built-in provider catalog (`anthropic`, `openai`, `google`, `native`).
- `set-role <role> <fast|workhorse|flagship> [--effort low|medium|high] [--scope global|project] [--cwd <path>]` — Sets a role's abstract tier (and optional effort override) across all platforms.
- `set-tier <platform> <fast|workhorse|flagship> --model <m> [--effort <e>] [--scope global|project] [--cwd <path>]` — Customizes what concrete model/effort a tier maps to on one platform.
- `set-override <platform> <role> (--model <m> [--effort <e>] | --clear) [--scope global|project] [--cwd <path>]` — Pins or clears a per-platform per-role override.
- `reset [--scope global|project|all] [--cwd <path>]` — Removes user/project customization and restores `balanced` defaults.
- `apply [--platform antigravity|claude|all] [--cwd <path>] [--home <dir>] [--target <dir>] [--dry-run]` — Compiles installed static agent profiles from `core/agents/` (or synced agent definitions) using the active global `~/.paddock/models.json` config without modifying tracked git files in the Paddock repo.

### 4.2 Chat Slash Command (`core/skills/paddock-models/SKILL.md`)

Invoked as `/paddock-models` (or natural-language model configuration requests) on both Antigravity and Claude Code:
1. Runs `show --cwd <workspace>` via the plugin's `paddock_core/models.py` script (falling back to `scripts/models.py` inside the Paddock repo) and displays the current effective model table.
2. Uses `ask_question` (or interactive prompt) to ask scope (`global` vs `project`) and action (`preset`, `set-provider`, `set-role`, `set-tier`, `set-override`, or `reset`).
3. Runs the corresponding `models.py` subcommand (`preset` / `set-provider` / `set-role` / `set-tier` / `set-override` / `reset`), runs `apply` when updating installed Claude static profiles globally, and prints the refreshed `show` output with exact CLI command + observed values.

---

## 5. Phase 1 Platform Integration

### 5.1 Antigravity (`platforms/antigravity/`)

- **`workflows/build_plan.py`:** Loads `models.py` (from `plugin_root/scripts/paddock_core/`, repo `core/config/`, or installed `~/.gemini/config/plugins/paddock/scripts/paddock_core/`), resolves `antigravity_subagent_alias(...)` (`flash`/`pro`/`inherit`/`flash_lite`) per role as base models (failing open to `DEFAULT_MODELS` = `flash` for `paddock-generalist`, `pro` for others), then overlays any explicit `Args.models` passed at workflow launch.
- **`hooks.json` & `scripts/agy_hooks.py`:**
  - `hooks.json`: extends the `PreToolUse` matcher to `invoke_subagent|run_workflow|swarm`.
  - `SessionStart`: appends one concise line showing effective Antigravity role models (`swarm_model` and `subagent_alias`) within the existing 19.5 KB cap.
  - `PreToolUse` on `invoke_subagent`: for `paddock-*` agents, resolves `subagent_alias` from `~/.paddock/models.json` (project then global). If the caller omitted `Model` or passed `"inherit"`, injects `"Model": <subagent_alias>` alongside `"Prompt"` in `PreToolUse` `overwrite` (when `<subagent_alias>` differs from `"inherit"`). Explicit caller `Model` values (e.g., `"pro"`) are preserved untouched.
  - `PreToolUse` on `swarm` (`action == "add"`): for Paddock worker names (`generalist`, `implementer`, `ui`, `planner`, `verifier`, `paddock-<role>`, or `<role>-<k>`), enforces `plan-gate` / `Mode: git` checks, injects `VERIFY_RULES` + matched traps into `brief`, and when `model` is omitted injects exact resolved `swarm_model` (`gemini-4-hydrogen-medium`, `gemini-4-hydrogen-high`, `gemini-4-neon-low`, `gemini-4-neon-medium`, `gemini-4-carbon-medium`, `claude-sonnet-5-5-*`, etc.) via `overwrite` while leaving explicit caller `model` values untouched.
- **`rules/AGENTS.md` & `skills/orchestrating/SKILL.md`:** Document using `swarm` for exact per-role concrete model+effort execution (`gemini-4-hydrogen-*`, `gemini-4-neon-*`, `gemini-4-carbon-*`, `claude-sonnet-5-5-*`) alongside deterministic `workflows/build_plan.py` (`run_workflow`) using safe `subagent_alias` fallback.

### 5.2 Claude Code (`platforms/claude/`)

- **`scripts/claude_hooks.py`:**
  - `SessionStart`: appends effective Claude role models (`model/effort`) and explicitly flags any project-level override whose resolved model differs from the installed frontmatter default.
  - `PreToolUse` on `Agent`: if a project-level override exists in `~/.paddock/projects/<slug>/models.json` for the dispatched Paddock role and differs from the static frontmatter, attaches a non-blocking `additionalContext` note telling Claude which `model` parameter to pass if supported or alerting the user that static profiles reflect global config.
- **`scripts/install.py` & `models.py apply`:** When installing or applying global config to Claude Code, renders `agents/*.md` cleanly from `core/agents/` + `~/.paddock/models.json`.

---

## 6. Security

- **Path containment & symlink refusal:** Config files may only be written to `<PADDOCK_HOME>/models.json` or `<PADDOCK_HOME>/projects/<slug>/models.json` (using `memory.repo_slug`). `apply` only writes exact known filenames (`<role>.md` or `paddock-<role>.md`) inside verified non-symlink `agents/` directories and rejects paths outside allowed roots or containing `..`.
- **Strict input validation:** Reject unknown platforms, built-in providers, roles, tiers, presets, and efforts; validate bare and `<provider>/<model-id>` concrete model strings against `^[a-z0-9][a-z0-9._-]{0,63}(?:/[a-z0-9][a-z0-9._-]{0,63})?$` (`^[a-z0-9][a-z0-9._-]{1,63}$` on Claude and Antigravity); reject explicit `effort` on bare Antigravity aliases (`inherit`, `flash_lite`, `flash`, `pro`), `".."`, multiple `/`, newlines, YAML control sequences, quotes, and shell metacharacters before serializing YAML frontmatter or JSON.
- **Atomic writes:** All JSON config and rendered agent markdown writes use closed `tempfile.mkstemp` + `os.replace` with UTF-8 LF encoding.
- **Fail-open hooks & workflows:** Corrupt, unreadable, or schema-mismatched `models.json` logs a warning to `stderr` and falls back to built-in `balanced` defaults without blocking tool execution.

---

## 7. Verification & Testing (`python -m pytest -q`)

- `tests/test_models_config.py`: `schema_version` checks, provider-agnostic `budget`/`balanced`/`quality` presets across all 5 platforms (including `antigravity` `google` default `gemini-4-hydrogen` / `gemini-4-neon` / `gemini-4-carbon` and `swarm_model` vs `subagent_alias` resolution), built-in multi-provider tier catalogs (`anthropic`, `openai`, `google`, `native`) with Google defaults on `antigravity` and Anthropic defaults on `copilot`/`pi`, `set-provider` switching (`antigravity` `google`/`anthropic`/`native`; `copilot`/`pi` `anthropic`/`openai`/`google`), effort resolution across all 5 platforms (clean ignore on bare Antigravity aliases), 6-step resolution precedence (call-time > project override > project tier > global override > global tier > default), partial project merge, strict validation/rejection of invalid models (`<provider>/<model-id>`)/providers/efforts/bare-alias-effort/traversal, atomic CLI subcommands (`show`, `preset`, `set-provider`, `set-role`, `set-tier`, `set-override`, `reset`), and corrupt-JSON fail-open fallback.
- `tests/test_agents_standard.py`: parses all `core/agents/*.md`, verifies capability-to-tool compilation for Antigravity and Claude Code, checks `apply` updates installed profiles without touching repo tracked files, and verifies symlink/traversal refusal.
- Extend `tests/test_sync_drift.py` (canonical `core/agents/` -> `platforms/*/agents/`, `core/config/` -> `paddock_core/`, `core/skills/paddock-models/`), `tests/test_agy_hooks.py` (`invoke_subagent` `subagent_alias` `Model` `overwrite` + `swarm` `action="add"` exact concrete `swarm_model` `overwrite` + `plan-gate` + `brief` traps + explicit `Model`/`model` preservation + `SessionStart` summary), `tests/test_agy_workflow.py` (`build_plan.py` resolving safe `subagent_alias` base models from concrete `models.json` + `Args.models` override), and `tests/test_claude_hooks.py` (`SessionStart` + project override `additionalContext`).
