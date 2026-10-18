---
name: finishing-branch
description: Use when implementation is complete, all tests pass, and you need to decide how to integrate the work - guides completion of development work by presenting structured options for merge, PR, or cleanup
phase: execution
flow-alternatives: [docs-generator]
related: [docs-generator, code-reviewer]
---

# Finishing Branch

## Overview

Guide completion of development work by presenting clear options and handling chosen workflow.

## Generated File Naming Convention (MANDATORY)

**ANY file created by this skill MUST be prefixed with `finishing-branch-`:**
- ✅ `finishing-branch-summary.md`, `finishing-branch-pr-description.md`
- ❌ `BRANCH_SUMMARY.md`, `PR_DESCRIPTION.md`

This applies to ALL generated files — summaries, reports.

**Core principle:** Verify tests → Present options → Execute choice → Clean up.

**Prerequisite:** Consider running `/docs-generator` first to update documentation before completing the branch.

**Announce at start:** "I'm using the finishing-branch skill to complete this work."

## The Process

### Step 1: Verify Tests

**Before presenting options, verify tests pass:**

```bash
pytest
ruff check .
mypy .
```

**If tests fail:** Stop. Don't proceed. Fix issues first.

**If tests pass:** Continue to Step 2.

### Step 2: Determine Base Branch

```bash
git merge-base HEAD main 2>/dev/null || git merge-base HEAD master 2>/dev/null
```

Or ask: "This branch split from main - is that correct?" **Use AskUserQuestion tool.**

### Step 3: Present Options

Present exactly these 4 options. **Use AskUserQuestion tool.**

```
Implementation complete. What would you like to do?

1. Merge back to <base-branch> locally
2. Push and open a Pull/Merge Request (in your Git host's web UI)
3. Keep the branch as-is (I'll handle it later)
4. Discard this work

Which option?
```

### Step 4: Execute Choice

#### Option 1: Merge Locally

```bash
git checkout <base-branch>
git pull
git merge <feature-branch>
pytest  # Verify on merged result
git branch -d <feature-branch>
```

Then: Cleanup worktree (Step 5)

#### Option 2: Push and Open a PR/MR

Host-agnostic — works on GitHub, GitLab, or Bitbucket. Push the branch, then open the
Pull/Merge Request in your Git host's web UI (the `git push` output usually prints a "create"
link you can open directly):

```bash
git push -u origin <feature-branch>
```

Suggested PR/MR description:

```markdown
## Summary
<2-3 bullets of what changed>

## Test Plan
- [ ] <verification steps>

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
```

Then: Report the branch name and the create-PR/MR URL from the push output.

#### Option 3: Keep As-Is

Report: "Keeping branch <name>. Worktree preserved at <path>."

**Don't cleanup worktree.**

#### Option 4: Discard

**Confirm first. Use AskUserQuestion tool.**
```
This will permanently delete:
- Branch <name>
- All commits
- Worktree at <path>

Type 'discard' to confirm.
```

Wait for exact confirmation. If confirmed:
```bash
git checkout <base-branch>
git branch -D <feature-branch>
```

### Step 5: Cleanup Worktree

**For Options 1, 2, 4:**

```bash
git worktree list | grep $(git branch --show-current)
# If in worktree:
git worktree remove <worktree-path>
```

**For Option 3:** Keep worktree.

## Quick Reference

| Option | Merge | Push | Keep Worktree | Cleanup Branch |
|--------|-------|------|---------------|----------------|
| 1. Merge locally | ✓ | - | - | ✓ |
| 2. Open PR/MR | - | ✓ | ✓ | - |
| 3. Keep as-is | - | - | ✓ | - |
| 4. Discard | - | - | - | ✓ (force) |

## Red Flags

**Never:**
- Proceed with failing tests
- Merge without verifying tests on result
- Delete work without confirmation
- Force-push without explicit request

---

## Next Steps

After branch is finished (merged, PR created, kept, or discarded), STOP.

**Workflow complete.** This is the terminal step — nothing auto-follows. See [[moc-finalization]] for phase context.

**Optional follow-ups:**
- [[/reflect]] `<TASK-ID: one-line summary of what you did>` - Capture any learnings or recurring mistakes from this task as durable rules.
- [[/docs-generator]] `<TASK-ID: one-line summary of what you did>` - Update documentation if it still needs it (best done *before* finishing).
