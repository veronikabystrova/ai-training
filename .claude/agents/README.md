# Agent-Based Skill Execution

This directory contains agent definitions that act as the **isolated execution
environment** for the **autonomous** workflow skills. Each such skill declares
`context: fork` and `agent: <name>`, so invoking it ALWAYS spawns the matching agent —
work never runs inline in the main thread.

**Exception — interactive skills run inline.** Skills that need live `AskUserQuestion`
dialogue (`brainstorm`, `git-worktrees`, `finishing-branch`,
`requirements-analyst`) cannot fork: a forked subagent has no access to the user and
runs once without a back-and-forth. These 4 skills run inline in the main thread and
therefore have **no agent**. Rule of thumb: **an agent exists ⟺ the skill forks.**

## Architecture: Skill → forked Agent

```
User invokes: /coder [prompt]
              │   skill frontmatter: context: fork, agent: coder
              ▼
    ┌──────────────────────────┐
    │   Isolated subagent       │  system prompt = agents/coder-agent.md
    │   (deterministic fork)     │  task         = skills/coder/SKILL.md
    └──────────────────────────┘
              │   ($ARGUMENTS carries the user's input into the fork)
              ▼
    ┌─────────────────────┐
    │   Work Performed     │   (all tool calls happen in the isolated context)
    └─────────────────────┘
              │
              ▼
    ┌─────────────────────┐
    │   Returns only       │
    │   Context Summary    │
    │   + Next Steps        │
    └─────────────────────┘
```

Invoking a skill (`/skill-name`, or Claude auto-triggering it by description)
**deterministically** forks into its agent via `context: fork`. The agent's markdown
body becomes the subagent's system prompt; the SKILL.md content becomes its task. The
main conversation stays clean — only the returned Context Summary comes back. The agent
does NOT re-invoke the skill (the task is already loaded); doing so would loop.

## Key Principles

### 1. Isolation
Each agent runs in its own context to prevent main conversation pollution and hallucination.

### 2. Stop After Completion
Agents complete their task and STOP. They do NOT automatically chain to next skills.

### 3. Context Handoff
Agents provide a short context summary that can be passed to the next command.

### 4. Next Step Suggestions
Every agent output ends with suggested next commands based on:
- The flow defined in `docs/SKILL-FLOW.md`
- Alternative relevant commands

### 5. Time Budget Awareness
If you've made >3 tool calls without measurable progress toward the goal, pause and reassess your approach. Consider:
- Is the current strategy working?
- Should you try an alternative approach?
- Should you escalate to `/debugger`?

## Agent Template

Each agent is the system prompt for its forked skill. It must NOT re-invoke the skill
(the SKILL.md content already arrives as the task via `context: fork`). Every agent
follows this structure:

```markdown
# [Skill Name] Agent

## Role
[Single sentence describing the agent's purpose]

## Instructions

1. Execute the task in your prompt directly and completely — it is the `[skill-name]`
   skill content, delivered via `context: fork`.
2. Do NOT re-invoke the `[skill-name]` skill; the task is already loaded, and
   re-invoking would loop.
3. STOP when the work is done.
4. Provide structured output (see below).

## Output Format

When done, provide:

### Context Summary
[2-3 sentences summarizing what was accomplished and key decisions/artifacts]

### Next Steps

**Next by flow:** `/[next-command]` - [one sentence why]

**Alternatives:**
- `/[alt-command-1]` - [one sentence why]
- `/[alt-command-2]` - [one sentence why]

## Constraints
- ONLY execute the given task
- DO NOT chain to other skills automatically
- DO NOT make workflow decisions
- STOP after completion and output suggestions
```

## Agent List

12 autonomous skills fork into an agent. The 4 interactive skills below run inline and
have no agent.

### Understanding Phase
| Agent | Skill | Next by Flow |
|-------|-------|--------------|
| `architect-agent` | architect | api-designer |

### Planning Phase
| Agent | Skill | Next by Flow |
|-------|-------|--------------|
| `api-designer-agent` | api-designer | writing-plans |
| `writing-plans-agent` | writing-plans | git-worktrees |

### Implementation Phase
| Agent | Skill | Next by Flow |
|-------|-------|--------------|
| `coder-agent` | coder | code-reviewer |

### Quality Phase
| Agent | Skill | Next by Flow |
|-------|-------|--------------|
| `code-reviewer-agent` | code-reviewer | test-generator |
| `test-generator-agent` | test-generator | debugger (if failures) / finishing-branch |
| `debugger-agent` | debugger | test-generator |
| `verify-agent` | verify | finishing-branch |

### Finalization Phase
| Agent | Skill | Next by Flow |
|-------|-------|--------------|
| `docs-generator-agent` | docs-generator | finishing-branch |

### Utility
| Agent | Skill | Next by Flow |
|-------|-------|--------------|
| `reflect-agent` | reflect | (standalone) |
| `skill-creator-agent` | skill-creator | (standalone) |

### Inline skills (no agent)
Two kinds of skill run in the main thread instead of forking:

- **Interactive** — they rely on live `AskUserQuestion` dialogue, which a forked subagent cannot host:
  `requirements-analyst`, `brainstorm` (Understanding), `git-worktrees` (Implementation),
  `finishing-branch` (Quality).
- **Reference utility** — `context7` (Utility) fetches up-to-date library docs via the `ctx7` CLI.
  It runs inline so the fetched docs land in the calling skill's context; a forked summary would
  discard them. So `an agent exists ⟺ the skill forks` still holds — `context7` doesn't fork, so it
  has no agent.

## Flow Reference

See `docs/SKILL-FLOW.md` for the complete visual flow.

## Context Splitting

Using agents splits context because:
1. Each agent runs as a subagent with fresh context
2. Only the context summary is returned to main conversation
3. Main agent stays clean without skill execution details
4. Prevents hallucination from context overload
