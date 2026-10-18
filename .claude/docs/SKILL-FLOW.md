---
title: Accelerator Skill Flow
type: index
description: Master navigation for the skill workflow.
---

# Skill Flow Reference

This document defines the recommended order of skills. Each skill stops after completion and suggests the next step - the user decides whether to continue.

The edges below mirror each skill's `flow-next` / `flow-alternatives` frontmatter (the source of truth). Every flow skill has at least one incoming edge — there are no orphans and no dead-ends. `reflect` and `skill-creator` are standalone utilities with no flow edges.

## Navigation

The workflow progresses through four phases. [[moc-understanding]] gathers and analyzes requirements. [[moc-planning]] designs architecture and APIs. [[moc-execution]] implements, reviews, and tests code. [[moc-finalization]] handles documentation and branch completion.

Utility tools ([[moc-utility]]) are available at any time.

## Flow Diagram

```
PHASE 1 — UNDERSTANDING                              Output: tasks/TASK-N/
  /requirements-analyst ──► /brainstorm

PHASE 2 — PLANNING                                   Output: specs/ + tasks/
  /architect ──► /api-designer ──► /writing-plans

PHASE 3 — EXECUTION
  /git-worktrees ──► /coder ──► /code-reviewer ──► /test-generator ──► /verify ──► /finishing-branch ──► (end)

  loops & fallbacks:
    /coder ───────────(needs investigation)──► /debugger
    /test-generator ──(tests fail)──► /debugger ──► /test-generator
    /debugger ────────(apply fix)───► /coder
    /verify ──────────(DoD fails)───► /coder  ·  /debugger  ·  /test-generator
    (most execution skills can also branch early to /finishing-branch once work is complete —
     see the Quick Reference table below for the full per-skill edge set)

PHASE 4 — FINALIZATION  (optional branch off /verify, before /finishing-branch)
  /verify ──(optional)──► /docs-generator ──► /finishing-branch
                           (README, ADRs)

UTILITY (any time, standalone — no flow edges)
  /reflect        /skill-creator        /context7
```

## Quick Reference: Next by Flow

The **Next by flow** column is the primary suggestion; **Alternatives / branches** lists the other
valid transitions a skill offers. Reading the two columns together gives the full edge set — so every
skill that appears as a destination is reachable.

| Current Skill             | Next by flow          | Alternatives / branches                                            |
| ------------------------- | --------------------- | ------------------------------------------------------------------ |
| [[/requirements-analyst]] | [[/brainstorm]]       | [[/architect]], [[/writing-plans]]                                 |
| [[/brainstorm]]           | [[/architect]]        | [[/api-designer]], [[/writing-plans]]                              |
| [[/architect]]            | [[/api-designer]]     | [[/writing-plans]], [[/coder]]                                     |
| [[/api-designer]]         | [[/writing-plans]]    | [[/git-worktrees]], [[/coder]], [[/test-generator]] (TDD)          |
| [[/writing-plans]]        | [[/git-worktrees]]    | [[/coder]], [[/architect]]                                         |
| [[/git-worktrees]]        | [[/coder]]            | —                                                                  |
| [[/coder]]                | [[/code-reviewer]]    | [[/test-generator]], [[/debugger]]                                 |
| [[/code-reviewer]]        | [[/test-generator]]   | [[/coder]], [[/finishing-branch]]                                  |
| [[/test-generator]]       | [[/verify]]           | [[/debugger]] (tests fail), [[/docs-generator]], [[/finishing-branch]], [[/coder]] |
| [[/debugger]]             | [[/test-generator]]   | [[/coder]], [[/docs-generator]], [[/code-reviewer]], [[/finishing-branch]] |
| [[/verify]]               | [[/finishing-branch]] | [[/docs-generator]] (optional finalization); [[/coder]] / [[/debugger]] / [[/test-generator]] (on fail) |
| [[/docs-generator]]       | [[/finishing-branch]] | [[/code-reviewer]]                                                 |
| [[/finishing-branch]]     | (end)                 | [[/docs-generator]] (if docs are still pending)                    |

> **Reaching the finalization phase:** `docs-generator` is an **optional** branch. The linear spine
> runs `/verify → /finishing-branch`; to update documentation first, detour from `/verify` (or take
> `/docs-generator` as an alternative from `/test-generator`). The branch converges back on
> `/finishing-branch`.

## Entry Points

Choose your starting point based on your situation:

| Situation                     | Start With                         |
| ----------------------------- | ---------------------------------- |
| Have requirements/specs       | [[/requirements-analyst]]          |
| Have an idea to explore       | [[/brainstorm]]                    |
| Have a plan ready             | [[/git-worktrees]] then [[/coder]] |
| Existing project, add feature | [[/git-worktrees]] then [[/coder]] |
| Fix a bug                     | [[/debugger]]                      |

## Utility Commands (Any Time)

Standalone workflows — they have no `flow-next`/`flow-alternatives` edges and do not feed into a phase.
After completion, return to whatever phase you were in.

| Command                 | When to Use                                          |
| ----------------------- | ---------------------------------------------------- |
| [[/reflect]]            | Turn agent mistakes into permanent rules             |
| [[/skill-creator]]      | Create a new skill                                   |
| [[/context7]]           | Fetch up-to-date library docs (runs inline, no agent) |

## How It Works

1. **User invokes a skill** (e.g., [[/requirements-analyst]] `[prompt]`)
2. **Autonomous skills fork into their agent** (`context: fork`) and run in an isolated context, so the main conversation stays clean. Interactive skills (brainstorm, git-worktrees, finishing-branch, requirements-analyst) run inline instead — a fork can't host their `AskUserQuestion` dialogue. The `context7` reference utility also runs inline (no agent) — a forked summary would discard the docs it fetches.
3. **The skill stops and outputs:**
   - Context summary (what was done)
   - Next step suggestion by flow
   - Alternative suggestions
4. **User decides** whether to run the next skill

## Context Handoff

Each skill's output includes a context summary. Pass this to the next skill:

```
/brainstorm Based on requirements analysis: [paste context summary]
```

This keeps the main conversation clean while preserving continuity.

## Example Flows

### New Backend Service (with finalization)

```
/brainstorm Design user management API
→ /architect Review architecture decisions
→ /api-designer Design REST endpoints and schemas
→ /writing-plans Create implementation tasks
→ /git-worktrees Create workspace
→ /coder Implement features
→ /code-reviewer Review code
→ /test-generator Generate tests
→ /verify Run the DoD checklist
→ /docs-generator Update documentation      (optional finalization branch)
→ /finishing-branch Complete
```

### Adding Feature to Existing Project

```
/requirements-analyst Parse new feature spec
→ /brainstorm Design the feature
→ /architect Review architecture
→ /writing-plans Create implementation tasks
→ /git-worktrees Create workspace
→ /coder Implement feature
→ /code-reviewer Review code
→ /test-generator Generate tests
→ /verify Run the DoD checklist
→ /finishing-branch Complete
```

### Fixing a Bug

```
/debugger Investigate the failing checkout flow
→ /test-generator Add a regression test
→ /verify Run the DoD checklist
→ /finishing-branch Complete
```
