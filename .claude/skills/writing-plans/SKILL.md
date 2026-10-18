---
name: writing-plans
context: fork
agent: writing-plans
description: Use when you have a spec or requirements for a multi-step task, before touching code. Creates detailed implementation plans with bite-sized tasks for engineers with zero codebase context.
phase: planning
flow-next: git-worktrees
flow-alternatives: [coder, architect]
related: [brainstorm, architect]
---

# Writing Plans

## Task

$ARGUMENTS

Carry out the request above using the guidance in this skill. When finished, STOP and emit the Context Summary + Next Steps.

## Overview

Write comprehensive implementation plans assuming the engineer has zero context for our codebase and questionable taste. Document everything they need to know: which files to touch for each task, code, testing, docs they might need to check, how to test it. Give them the whole plan as bite-sized tasks. DRY. YAGNI. TDD. Frequent commits.

**Announce at start:** "I'm using the writing-plans skill to create the implementation plan."

## Generated File Naming Convention (MANDATORY)

**ANY file created by this skill MUST be prefixed with `writing-plans-`.**
Predefined output (`writing-plans-plan.md`) already follows this convention.
Any additional ad-hoc files (summaries, notes) MUST also follow this rule:
- ✅ `writing-plans-task-breakdown.md`
- ❌ `TASK_BREAKDOWN.md`

## Task Numbering

**Before creating the plan:**

1. **Check if task number provided:**
   - If coming from `/requirements-analyst` or `/brainstorm`, use the task number from context (e.g., "TASK-001")
   - If no task number: run task counter logic

2. **Task counter logic (if no task number provided):**
   - If `tasks/.task-counter` exists: read the number, use it, increment and write back
   - If missing: scan `tasks/` for existing `TASK-*` directories, use max(N) + 1, create counter file

3. **Create task directory:** `tasks/TASK-{N}/` (if not already created)

4. **Save plan to:** `tasks/TASK-{N}/writing-plans-plan.md`

## Bite-Sized Task Granularity

**Each step is one action (2-5 minutes):**
- "Write the failing test" - step
- "Run it to make sure it fails" - step
- "Implement the minimal code to make the test pass" - step
- "Run the tests and make sure they pass" - step
- "Commit" - step

## Plan Document Header

**Every plan MUST start with this header:**

```markdown
# [Feature Name] Implementation Plan

**Task:** TASK-{N}

> **For Claude:** Use `git-worktrees` to create isolated workspace, then implement with `coder` skill.

**Goal:** [One sentence describing what this builds]

**Architecture:** [2-3 sentences about approach]

**Tech Stack:** [Key technologies/libraries]

---
```

## Task Structure

```markdown
### Task N: [Component Name]

**Files:**
- Create: `exact/path/to/file.py`
- Modify: `exact/path/to/existing.py:123-145`
- Test: `tests/test_file.py`

**Step 1: Write the failing test**

\`\`\`python
def test_does_specific_thing() -> None:
    result = do_thing(input_value)
    assert result == expected
\`\`\`

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_file.py`
Expected: FAIL with "function not defined" / ImportError

**Step 3: Write minimal implementation**

\`\`\`python
def do_thing(value: InputType) -> OutputType:
    return expected
\`\`\`

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_file.py`
Expected: PASS

**Step 5: Commit**

\`\`\`bash
git add path/to/files
git commit -m "feat(domain): add specific feature"
\`\`\`
```

## Project Structure Reference

When creating plans for new projects, use these standard structures:

### Python Service (src layout)

```
project-root/
├── pyproject.toml            # project + tool config (uv, ruff, mypy, pytest)
├── README.md
├── src/<package>/
│   ├── __init__.py
│   ├── main.py               # app/CLI entrypoint
│   ├── core/                 # config, logging, db session, errors
│   │   ├── config.py         # settings (env-driven)
│   │   ├── logging.py
│   │   ├── db.py             # engine/session (SQLAlchemy) or equivalent
│   │   └── exceptions.py
│   └── <feature>/
│       ├── __init__.py
│       ├── api.py            # route handlers / views
│       ├── service.py        # business logic
│       ├── repository.py     # data access
│       ├── schemas.py        # request/response models
│       └── models.py         # ORM models / domain entities
└── tests/
    ├── conftest.py
    └── <feature>/
        └── test_service.py
```

Django projects map `<feature>/` onto an app (`models.py`, `views.py`, `serializers.py`, `urls.py`). Keep the layering regardless of the framework's file conventions.

## Remember

- Exact file paths always
- Complete code in plan (not "add validation")
- Exact commands with expected output
- Reference relevant skills with backticks
- DRY, YAGNI, TDD, frequent commits

## Execution Handoff

After saving the plan, offer execution choice:

**"Plan complete and saved to `tasks/TASK-{N}/writing-plans-plan.md`. Execution options:**

**1. Execute Now** - Use `/coder` to implement in current workspace

**2. Isolated Workspace** - Use `/git-worktrees` to create isolated workspace, then `/coder`

**Which approach?"**

---

## Next Steps

After the plan is complete and saved, STOP and present these options:

**Next by flow:** [[/git-worktrees]] `<TASK-{N}: one-line summary of what you did>` - Create an isolated workspace for development. See [[moc-execution]] for phase context.

**Pass to next skill:** Include the task number in your context summary (e.g., "TASK-001: User authentication plan created")

**Alternatives:**
- [[/coder]] `<TASK-{N}: one-line summary of what you did>` - Start implementing directly in the current workspace (skip the worktree).
- [[/architect]] `<TASK-{N}: one-line summary of what you did>` - Revisit architecture decisions if the plan exposed gaps.
