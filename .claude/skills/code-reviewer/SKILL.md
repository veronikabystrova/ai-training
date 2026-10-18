---
name: code-reviewer
context: fork
agent: code-reviewer
description: Review code for quality, standards compliance, security issues, and performance problems. Use when reviewing PRs, checking code quality, finding bugs, or ensuring standards compliance. Triggers on "review code", "code review", "check this", "review PR", "find issues", "code quality".
phase: execution
flow-next: test-generator
flow-alternatives: [coder, finishing-branch]
related: [coder]
---

# Code Reviewer

## Task

$ARGUMENTS

Carry out the request above using the guidance in this skill. When finished, STOP and emit the Context Summary + Next Steps.

## Overview

Review code for quality, standards compliance, security issues, and performance problems.

## Generated File Naming Convention (MANDATORY)

**ANY file created by this skill MUST be prefixed with `code-reviewer-`:**
- ✅ `code-reviewer-report.md`, `code-reviewer-findings.md`
- ❌ `REVIEW_REPORT.md`, `FINDINGS.md`

This applies to ALL generated files — review reports, summaries, findings.

## Review Categories

### 1. Architecture Compliance

- [ ] Correct layer placement (handler, service, repository)
- [ ] Dependencies flow in correct direction
- [ ] Proper module organization
- [ ] Single responsibility principle

### 2. Code Quality

- [ ] Clear naming conventions
- [ ] No magic numbers/strings
- [ ] DRY - no duplicate code
- [ ] YAGNI - no unnecessary features
- [ ] Proper error handling
- [ ] Comments only where logic is non-obvious

### 3. Python Typing & Idioms

- [ ] Precise type hints on public functions (no bare `Any`)
- [ ] `@dataclass`/Pydantic for structured data instead of loose dicts
- [ ] Explicit return types (including `-> None`)
- [ ] Proper `None` handling (narrowing before attribute access)
- [ ] No mutable default arguments (`def f(x=[])`); use `None` + init in body
- [ ] Context managers (`with`) for resources; no leaked file/DB handles
- [ ] No abuse of `# type: ignore`; `mypy` passes

### 4. Security

- [ ] Input validation present
- [ ] No hardcoded secrets
- [ ] Proper authorization checks
- [ ] SQL injection prevention (parameterized queries)
- [ ] XSS prevention (output encoding)

### 5. Performance

- [ ] No N+1 queries
- [ ] Proper indexing considered
- [ ] Efficient algorithms
- [ ] No memory leaks
- [ ] Async operations where appropriate

### 6. Testing

- [ ] Unit tests present (`test_*.py`)
- [ ] Edge cases covered
- [ ] Tests are readable
- [ ] Mocks/fixtures used appropriately

## Code Smells

| Smell | Issue | Fix |
|-------|-------|-----|
| Long method | Hard to understand | Extract to smaller methods |
| Large class | Too many responsibilities | Split into focused classes |
| Feature envy | Method uses other class data | Move method to that class |
| Data clumps | Same data groups repeated | Create a class for it |
| Primitive obsession | Using primitives for domain concepts | Create value objects |
| Switch statements | Often indicates missing polymorphism | Use strategy pattern |
| Parallel inheritance | Two hierarchies change together | Merge or compose |
| Dead code | Unused code | Delete it |
| Comments | Explaining bad code | Refactor to be self-documenting |

## Review Process

### Step 1: Understand Context

- What is the purpose of this change?
- What requirements does it fulfill?
- What existing code does it interact with?

### Step 2: Check Architecture

- Does it follow project patterns?
- Is it in the correct layer?
- Are dependencies correct?

### Step 3: Review Logic

- Does the code do what it's supposed to?
- Are edge cases handled?
- Are errors handled properly?

### Step 4: Check Tests

- Are there tests?
- Do tests cover the functionality?
- Are tests readable and maintainable?

### Step 5: Pre-Completion Verification

Cross-reference against [DOD.md](../../DOD.md) (Standard tier):
- Verify tests exist and pass (if test tooling present)
- Verify lint passes (if lint tooling present)
- Verify file naming follows skill-prefix convention
- Include verification evidence in review output

### Step 6: Provide Feedback

Format feedback as:

```markdown
## Summary
[Overall assessment]

## Issues Found

### Critical (Must Fix)
- [ ] [Issue description] - [File:Line]

### Major (Should Fix)
- [ ] [Issue description] - [File:Line]

### Minor (Consider)
- [ ] [Issue description] - [File:Line]

### Positive Notes
- [What was done well]
```

## Severity Guidelines

| Severity | Criteria | Examples |
|----------|----------|----------|
| Critical | Security issue, data loss, crash | SQL injection, missing auth, unhandled exception on hot path |
| Major | Bug, poor performance, maintainability | N+1 query, duplicate code, swallowed exception |
| Minor | Style, naming, small improvements | Variable naming, comments, missing type hint |

### Example finding

Write each issue as `severity` + `location` + what's wrong + the fix, so it is actionable:

> **[Major]** `app/services/order.py:42` — N+1 query: `get_orders()` loads each order's `items`
> lazily inside the loop, issuing one query per row.
> **Fix:** eager-load with `select(Order).options(selectinload(Order.items))`.

> **[Critical]** `app/api/users.py:88` — endpoint trusts the client-supplied `is_admin` field from the
> request body, allowing privilege escalation.
> **Fix:** drop `is_admin` from the input schema; set roles server-side from the authenticated principal.

---

## Final Output (MANDATORY)

**Before presenting Next Steps, persist the review to a file:**

- Use the task number from context (e.g. `TASK-001`) and write the full feedback
  (Summary, Issues by severity, Positive Notes, Verification Evidence) to
  `tasks/TASK-{N}/code-reviewer-findings.md`.
- This keeps the review durable (survives a context clear) and hands it cleanly to
  `/coder` or `/debugger`. Returning the review only inline loses it on `/clear`.
- If no task number is in context, save `code-reviewer-findings.md` in the repo root
  (still skill-prefixed).

---

## Next Steps

After code review is complete, STOP and present these options:

**Next by flow:** [[/test-generator]] `<TASK-ID: one-line summary of what you did>` - Generate tests for the reviewed code. See [[moc-execution]] for phase context.

**Alternatives:**
- [[/coder]] `<TASK-ID: one-line summary of what you did>` - Fix issues identified in the review.
- [[/finishing-branch]] `<TASK-ID: one-line summary of what you did>` - Complete branch if review passes and tests exist.
