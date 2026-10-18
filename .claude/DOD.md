# Definition of Done (DoD)

Tiered checklist. Every item is verifiable by command. Commands are conditional: run if tooling exists, else report missing.

## Minimum (every task)

- [ ] No uncommitted changes that belong to the task: `git diff --stat`
- [ ] File naming follows skill-prefix convention: check `tasks/` and `specs/` files
- [ ] Context Summary provided with 2-3 sentences + Next Steps

## Standard (implementation tasks)

All Minimum items, plus:

- [ ] Tests pass: `pytest` (or `uv run pytest`) (if a test suite exists)
- [ ] Lint passes: `ruff check .` (if present)
- [ ] Formatting clean: `ruff format --check .` (if present)
- [ ] No type errors: `mypy .` (if configured)
- [ ] New code has test coverage (at least happy path): `pytest --cov` and confirm a `test_*.py` exists for the change
- [ ] No OWASP Top 10 vulnerabilities introduced (manual review — check input validation, auth, injection points)
- [ ] Code reviewed or self-reviewed against GOLDEN-PRINCIPLES.md (manual review — if file exists)

## Full (release / merge to main)

All Standard items, plus:

- [ ] All CI checks pass (check your CI/CD pipeline / dashboard)
- [ ] PR description includes summary and test plan
- [ ] Specs updated if architecture/API changed
- [ ] No TODO/FIXME/HACK comments left unresolved: `grep -rn "TODO\|FIXME\|HACK" <src-dirs>`
- [ ] Documentation updated for user-facing changes (manual review)

## How to Use

1. Before marking a task complete, run the applicable tier's checks
2. If a command is not available (no test runner, no linter), note it as "N/A — tooling not configured"
3. If a check fails, fix it or escalate to `/debugger`
4. Include verification evidence in your Context Summary
5. **Verify gates independently — do not trust self-reports.** A pass/fail status MUST come from running the command in *this* verification, with its actual output as evidence. Never rely on an upstream skill's claim of "all green" — a forked skill's self-report is not proof; re-run the gate. (Scope the run to the change, e.g. `mypy app tests`, so checks aren't silently skipped.)
