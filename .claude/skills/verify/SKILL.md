---
name: verify
context: fork
agent: verify
description: Run full Definition of Done checklist and report pass/fail status. Use before merging, creating PRs, or claiming task completion. Triggers on "verify", "check DoD", "pre-merge check", "readiness check".
phase: execution
flow-next: finishing-branch
flow-alternatives: [docs-generator, coder, debugger, test-generator]
related: [code-reviewer, test-generator]
---

# Verify

## Task

$ARGUMENTS

Carry out the request above using the guidance in this skill. When finished, STOP and emit the Context Summary + Next Steps.

## Overview

Run the full Definition of Done checklist from `.claude/DOD.md` and produce a structured pass/fail report. This is the final gate before merge or PR creation.

## Generated File Naming Convention (MANDATORY)

**ANY file created by this skill MUST be prefixed with `verify-`:**
- `verify-report.md`, `verify-checklist.md`

## Verification Workflow

### Step 1: Determine Tier

- If this is a release/merge to main: **Full** tier
- If this is an implementation task: **Standard** tier
- Otherwise: **Minimum** tier

### Step 2: Run Checks

For each applicable check in the tier, run the command and record result:

#### Minimum Checks
1. `git diff --stat` — any uncommitted changes?
2. Check file naming in `tasks/` and `specs/` — all files use skill prefix?
3. Context Summary present?

#### Standard Checks (adds to Minimum)
4. `pytest` (or `uv run pytest`) — tests pass?
5. `ruff check .` — lint clean?
6. `ruff format --check .` — formatting clean?
7. `mypy .` — type errors? (if configured)
8. New code has test coverage (`pytest --cov`)?
9. No OWASP Top 10 vulnerabilities?

#### Full Checks (adds to Standard)
10. CI checks passing? (check your CI/CD pipeline / dashboard)
11. PR description has summary + test plan?
12. Specs updated if architecture/API changed?
13. No unresolved TODO/FIXME/HACK?
14. Documentation updated for user-facing changes?

### Step 3: Handle Missing Tooling

If a command is not available (no test runner, no linter, no build script):
- Record as `N/A — tooling not configured`
- Do NOT fail the check
- Do NOT try to install missing tooling

### Step 4: Generate Report

```markdown
## Verification Report

**Tier:** Standard
**Date:** YYYY-MM-DD
**Branch:** feature/xyz

| # | Check | Status | Details |
|---|-------|--------|---------|
| 1 | Uncommitted changes | PASS | Clean working tree |
| 2 | File naming | PASS | All files prefixed |
| 3 | Tests | FAIL | 2 failing in tests/test_user_service.py |
| 4 | Lint | PASS | No issues |
| ... | ... | ... | ... |

**Result:** FAIL (1 blocking issue)

### Blocking Issues
- Tests: 2 failures in tests/test_user_service.py — run `/debugger` to investigate

### Recommendations
- Fix test failures before merging
```

### Step 5: Actionable Output

- If all checks PASS: Recommend proceeding to `/finishing-branch`
- If any checks FAIL: List specific fix actions with commands

### Step 6: Persist the Report (MANDATORY)

Write the full report (the table + result + blocking issues) to a file so the DoD
verdict is durable evidence, not just inline text:

- Use the task number from context and write to `tasks/TASK-{N}/verify-report.md`.
- If no task number is in context, save `verify-report.md` in the repo root (still skill-prefixed).
- Every status in the report MUST be backed by the command output you actually ran in
  this verification — never carry over an upstream skill's self-reported "green" (see
  `.claude/DOD.md` → "Verify gates independently").

---

## Next Steps

After verification is complete, STOP and present these options:

**If PASS — Next by flow:** [[/finishing-branch]] `<TASK-ID: one-line summary of what you did>` - Complete branch work, create PR or merge.

**If PASS — Before finishing (optional finalization):**
- [[/docs-generator]] `<TASK-ID: one-line summary of what you did>` - Update README / ADRs / docstrings for the change.

**If FAIL — Alternatives:**
- [[/coder]] `<TASK-ID: one-line summary of what you did>` - Fix implementation issues found during verification.
- [[/debugger]] `<TASK-ID: one-line summary of what you did>` - Debug failing tests or build issues.
- [[/test-generator]] `<TASK-ID: one-line summary of what you did>` - Add missing test coverage.
