---
name: git-worktrees
description: Use when starting feature work that needs isolation from current workspace - creates isolated git worktrees for parallel development
phase: execution
flow-next: coder
flow-alternatives: []
related: [writing-plans, coder]
---

# Using Git Worktrees

## Overview

Git worktrees create isolated workspaces sharing the same repository, allowing work on multiple branches simultaneously without switching. Useful for developing independent features in parallel.

**Core principle:** Systematic directory selection + safety verification = reliable isolation.

**Announce at start:** "I'm using the git-worktrees skill to set up isolated workspace(s)."

## Step 1: Determine Worktree Type

**Ask user what worktrees to create. Use AskUserQuestion tool:**

```
How would you like to organize your worktree(s)?

1. Single worktree
   Best for: one focused feature or fix in an isolated branch.

2. Separate worktrees (multiple)
   Best for: working on several independent features in parallel, or by
   different sessions, when each has its own lifecycle and branch.

Which option?
```

**Branch naming convention:**
- Single: `feature/<name>`
- Separate (parallel): `feature/<name-a>` and `feature/<name-b>`

## Step 2: Directory Selection

Follow this priority order:

### 2.1. Check Existing Directories

```bash
# Check in priority order
ls -d .worktrees 2>/dev/null     # Preferred (hidden)
ls -d worktrees 2>/dev/null      # Alternative
```

**If found:** Use that directory. If both exist, `.worktrees` wins.

### 2.2. Check CLAUDE.md

```bash
grep -i "worktree.*director" CLAUDE.md 2>/dev/null
```

**If preference specified:** Use it without asking.

### 2.3. Ask User

If no directory exists and no CLAUDE.md preference. **Use AskUserQuestion tool.**

```
No worktree directory found. Where should I create worktrees?

1. .worktrees/ (project-local, hidden)
2. ~/worktrees/<project-name>/ (global location)

Which would you prefer?
```

## Step 3: Safety Verification

### For Project-Local Directories

**MUST verify directory is ignored before creating worktree:**

```bash
git check-ignore -q .worktrees 2>/dev/null
```

**If NOT ignored:**
1. Add to .gitignore
2. Commit the change
3. Proceed with worktree creation

## Step 4: Create Worktree(s)

### Single Worktree

```bash
git worktree add <worktree-dir>/<feature-name> -b feature/<feature-name>
cd <worktree-dir>/<feature-name>
```

### Separate Worktrees (parallel features)

```bash
git worktree add <worktree-dir>/<feature-a> -b feature/<feature-a>
git worktree add <worktree-dir>/<feature-b> -b feature/<feature-b>
```

## Step 5: Run Project Setup (each worktree)

Auto-detect and install dependencies in each worktree, preferring the project's
declared toolchain:

```bash
# uv (preferred): syncs the locked environment into .venv
if [ -f uv.lock ] || [ -f pyproject.toml ]; then
  uv sync
# Poetry
elif [ -f poetry.lock ]; then
  poetry install
# Pipenv
elif [ -f Pipfile.lock ]; then
  pipenv install --dev
# Plain pip
elif [ -f requirements.txt ]; then
  python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
fi
```

## Step 6: Verify Clean Baseline (each worktree)

Run the test suite to ensure the worktree starts clean:

```bash
uv run pytest    # or: pytest
```

**If tests fail:** Report failures, ask whether to proceed or investigate. **Use AskUserQuestion tool.**

**If tests pass:** Report ready.

## Step 7: Report Location(s)

### Single Worktree

```
Worktree ready at <full-path>
Branch: feature/<feature-name>
Dependencies installed (uv sync)
Tests passing (<N> tests, 0 failures)
Ready to implement <feature-name>
```

### Separate Worktrees

```
Worktrees ready for parallel development:

<feature-a>:
  Path: <full-path-a>
  Branch: feature/<feature-a>
  Tests: passing (<N> tests)

<feature-b>:
  Path: <full-path-b>
  Branch: feature/<feature-b>
  Tests: passing (<N> tests)

Ready for parallel implementation.
```

## Quick Reference

| Situation | Action |
|-----------|--------|
| `.worktrees/` exists | Use it (verify ignored) |
| `worktrees/` exists | Use it (verify ignored) |
| Both exist | Use `.worktrees/` |
| Neither exists | Check CLAUDE.md → Ask user (Use AskUserQuestion tool) |
| Directory not ignored | Add to .gitignore + commit |
| Tests fail during baseline | Report failures + ask (Use AskUserQuestion tool) |
| One focused feature | Single worktree |
| Several independent features | Separate worktrees |

## Common Mistakes

- **Skipping ignore verification** - Worktree contents get tracked
- **Assuming directory location** - Creates inconsistency
- **Proceeding with failing tests** - Can't distinguish new bugs from pre-existing
- **Not asking user preference** - Different features benefit from different setups
- **Installing into the wrong environment** - Run `uv sync` (or the project's setup) *inside* each worktree so each has its own `.venv`

---

## Next Steps

This skill runs **inline** (no forked agent relays its result), so you MUST surface the hand-off in
the **chat** yourself. After worktree(s) are created and verified, STOP and present **in the chat**:
first a short **Context Summary** (the real `TASK-{N}` id + worktree path(s) + a one-line summary),
then these options:

**Next by flow:** [[/coder]] `<TASK-ID: one-line summary of what you did>` - Start implementation in the new worktree. See [[moc-execution]] for phase context.

**For separate worktrees:** Run [[/coder]] `<TASK-ID: one-line summary of what you did>` in each worktree for parallel feature development.
