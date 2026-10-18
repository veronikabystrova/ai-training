# Claude Code — Project Memory

Policy rules live in `AGENTS.md` (single source of truth, also read by other agents).
They are imported below so Claude Code loads them at session start.

@../AGENTS.md

## Claude Code specifics

- Skills are the primary surface: invoke `/skill-name` or let descriptions auto-trigger.
- Autonomous skills declare `context: fork` + `agent:`, so invoking them ALWAYS runs in an isolated agent that returns a Context Summary + Next Steps — never inline, no auto-chaining. The 4 interactive skills (brainstorm, git-worktrees, finishing-branch, requirements-analyst) run inline with no agent — a fork can't host their `AskUserQuestion` dialogue. The `context7` reference utility also runs inline with no agent — it injects fetched docs into the calling context, which a forked summary would discard.
- Hooks receive tool-call JSON on **stdin** (not via env vars).
- Toolchain: **uv + ruff + pytest + mypy**.
