---
title: Execution Phase
type: moc
phase: 3
description: Implement, review, test, debug, and complete code in isolated workspaces.
---

# Execution Phase

The third phase implements the plans from [[moc-planning]] in isolated git worktrees. It follows a build-review-test loop until the code is ready for [[moc-finalization]].

## Commands

[[/git-worktrees]] creates isolated git workspaces for feature development, enabling parallel work on multiple branches without disturbing the current workspace.

[[/coder]] implements features following a layered architecture (API/handlers → services → data access).

[[/code-reviewer]] reviews code for quality, standards compliance, security issues, and performance problems.

[[/test-generator]] generates comprehensive tests (unit, integration, E2E) and runs test suites with failure analysis.

[[/debugger]] investigates bugs, test failures, and unexpected behavior through systematic root cause analysis before proposing fixes.

[[/finishing-branch]] completes development work by presenting structured options for merge, PR, or cleanup.

## Flow

The default path is [[/git-worktrees]] then [[/coder]] then [[/code-reviewer]] then [[/test-generator]] then [[/finishing-branch]]. The [[/debugger]] is used when tests fail, creating a loop: [[/debugger]] then [[/test-generator]] until all tests pass. Independent features can run in parallel using separate worktrees.

## Loop Pattern

```
/coder -> /code-reviewer -> /test-generator
  -> (tests pass?) -> /finishing-branch
  -> (tests fail?) -> /debugger -> /test-generator
```

## Next Phase

Continue to [[moc-finalization]] to update documentation and create releases.
