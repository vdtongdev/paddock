---
name: antipatterns
description: Guardrails against scope creep, code smells, over-abstracted GoF patterns, MVC layering violations, and agent workflow mistakes.
---

# Engineering & Workflow Antipatterns

Apply these checks before writing or approving code across any language or framework.

## 1. Scope & Simplicity

- **Spec-only changes:** Implement only what the task and spec ask for. Do not refactor unrelated modules, rename untouched symbols, or add speculative config flags.
- **YAGNI:** Prefer one concrete function or module over premature plugin registries, factories, or multi-layer wrappers with one implementation.
- **No silent fallbacks:** Never swallow exceptions or return fake defaults on I/O, auth, or validation failure unless fail-open behavior is explicitly specified (such as lifecycle hooks).

## 2. Code Smells & Modularity

- **Zero duplicated logic:** If two code blocks share structure or rules, extract one parameterized helper.
- **Shallow nesting & small functions:** Use early returns to keep control flow flat and readable.
- **Uniform variant handling:** Replace long `if/elif/else` chains over formats, events, or types with dispatch tables or mappings.
- **Reuse existing helpers:** Inspect sibling modules before reimplementing parsing, path normalization, atomic writes, or subprocess wrappers.

## 3. GoF & Layering Discipline

- **Patterns solve concrete pain:** Never introduce Strategy, Visitor, AbstractFactory, or Observer unless multiple independent variants already exist.
- **Clean boundaries:** Keep storage/domain logic (`core/memory/`), protocol framing (`mcp_server.py`), and platform adapters (`platforms/*/`) decoupled; adapters translate payloads and delegate business rules to `core/`.

## 4. Agent Workflow Mistakes

- **Never fix bugs blind:** Start with `Mode: diagnose` (failing regression test + root cause `file:line`).
- **Never skip approval gates:** Stop after spec authoring (Gate 1) and after plan review (Gate 2).
- **Never guess unseen output:** Run the exact check command after the final edit and quote observed output.
- **Never pollute target repos:** Keep memory under `~/.paddock/` and local worktree exclusions in `.git/info/exclude` (never tracked `.gitignore`).
