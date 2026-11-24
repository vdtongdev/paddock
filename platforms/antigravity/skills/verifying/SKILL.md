---
name: verifying
description: Verification contract requiring observed-this-session evidence, exact ## Evidence and ## Unverified sections, and typed verdicts.
---

# Verification & Evidence Contract

No claim of completion, pass, or correctness is valid without direct evidence observed in the current session after the final edit.

## 1. Observed-This-Session Rule

Accepted evidence formats only:
- **Command + output:** Exact command run after the final edit and its observed exit code, counts, or output lines.
- **File + line:** Verified file path and line range (`path:start-end`) read directly in this session.
- **URL + quote:** Fetched/read external reference with exact quoted text.

Forbidden:
- Reporting tests as passing from memory, prior turns, or pre-edit runs.
- Extrapolating skipped, mocked, or filtered runs as full suite passes.
- Marking any memory entry `verified: human` unless a human explicitly confirmed the fact (model-verified facts use `verified: model`).

## 2. Required Report Structure

Every worker completion and verifier report must include:

```markdown
## Evidence
- `<command>`: exit <code> — <observed output summary>
- `<path>:<lines>` — <verified behavior>

## Unverified
- <Anything not run, skipped, mocked, or missing soft dependency; write "None." if everything required was checked.>
```

## 3. Verifier Verdict Line

Verifiers must end with **exactly one** verdict token matching the loop contract:
- `PASS`
- `FAIL: impl`
- `FAIL: plan`
- `FAIL: spec`
- `FAIL: env`

(Note: when returning structured JSON inside a workflow `finish` tool, populate `verdict`, `summary`, `evidence`, and `unverified` directly with the exact same discipline.)

## 4. UI Soft-Dependency Check (`impeccable`)

For UI targets, run `npx impeccable detect --json <target>` if available:
- Exit `0`: include output under `## Evidence`.
- Exit `2`: emit `FAIL: impl` with findings.
- Exit `1`: emit `FAIL: env`.
- Missing CLI/skill: note under `## Unverified` without failing the task solely for absence.
