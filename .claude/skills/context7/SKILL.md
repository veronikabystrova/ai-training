---
name: context7
description: Fetch up-to-date, version-specific library/framework documentation and code examples via the Context7 CLI. Use when you need current API docs for a library (SQLAlchemy, FastAPI, Pydantic, Django, etc.) instead of relying on training-cutoff knowledge. Triggers on "look up docs", "context7", "use context7", "latest docs for", "up-to-date docs", "how does <library> API work".
phase: utility
related: [coder, architect, api-designer, debugger]
---

# Context7

## Task

$ARGUMENTS

Fetch current documentation for the library/topic above using the Context7 CLI, then use
the returned docs to inform the work you were doing. STOP and return to your prior skill.

## Overview

[Context7](https://github.com/upstash/context7) (Upstash) pulls up-to-date, version-specific
documentation and code examples straight from a library's source and feeds them into your context.
Use it to ground implementation, design, and debugging decisions in the **current** API instead of
training-cutoff knowledge.

This is a **lightweight, inline reference utility** — it does NOT fork into an agent. The docs it
fetches land in your *current* context (e.g. inside the `coder` or `architect` agent that called it),
which is the whole point: a forked summary would discard them. It writes no files and makes no
workflow decisions.

## When to use

- Implementing or designing against an **unfamiliar or fast-moving** library API (web frameworks,
  ORMs, validation libs, cloud SDKs).
- You need **version-specific** behavior or a current code example, not a remembered one.
- A `code-reviewer`/`debugger` comparison hinges on whether code matches the library's **current**
  recommended usage.

## When to skip

- The Python **standard library** or stable, well-known APIs.
- **Project-internal** code — read the codebase instead.
- A pattern the **existing code already demonstrates** — follow that.

## How to use (two-step CLI)

The CLI ships as `ctx7` and is run via `npx` (Node.js 18+ required — no install/copy into the repo):

**Step 1 — resolve the library to a Context7 ID:**

```bash
npx -y ctx7 library fastapi "dependency injection"
```

This returns matching libraries with their Context7 IDs (e.g. an ID like `/tiangolo/fastapi`).
Pick the ID that matches the library and version you want.

**Step 2 — fetch focused docs for that ID:**

```bash
npx -y ctx7 docs /tiangolo/fastapi "Depends and dependency overrides"
```

Keep the query **focused** (one concept) so the returned docs stay relevant and compact. Then apply
what you learned and continue your task.

## API key (optional)

Context7 works anonymously but rate-limits. For higher limits, get a free key at
`context7.com/dashboard` and export it before running the CLI:

```bash
export CONTEXT7_API_KEY="..."
```

Never hardcode or commit the key (see AGENTS.md — no secrets in the repo). It is a per-developer
environment value, not part of the framework copy set.

## Graceful degradation

Context7 is best-effort. If `npx`/Node is missing, the network is unavailable, or the lookup returns
nothing useful, **do not block the task** — fall back to:

1. patterns already present in the codebase, then
2. `WebFetch` of the library's official documentation site.

Note the fallback in your output so the choice is visible.

## No file output

This skill returns docs into context only; it creates no `tasks/`/`specs/` artifacts, so the
skill-prefix naming convention does not apply to its runtime.

---

## Next Steps

Context7 is a standalone utility with no flow edges. After the docs are in context, **return to the
skill you were working in** (e.g. [[/coder]], [[/architect]], [[/api-designer]], or [[/debugger]]) and
continue from where you paused. See [[moc-utility]] for utility context.
