---
name: gh-issue
description: Create a GitHub issue for the accelerator-mini-python repo with the project's unified style. Use whenever the user asks to open/file/create an issue, log a bug, or capture a backlog/tech-debt task — and whenever you decide to track work as an issue before a PR. Keeps titles, labels and body structure consistent. Activates on "создай issue", "заведи задачу", "открой issue", "запиши баг", "create an issue", "file a bug", "log a task".
---

# Creating a GitHub issue for accelerator-mini-python

The golden rule: **lean but unified.** Every issue uses the same small set of
labels and the same body shape so the backlog stays scannable. This is a
solo/tiny-team repo — no milestones, no projects, no priority/status labels, no
assignees. An **open** issue = todo; a **closed** issue = done. That's the whole
system.

> This repo is **Accelerator Core** — a Claude Code *framework* (skills, agents,
> hooks, docs under `.claude/` + root policy files), not an application. There is
> no app source tree, no frontend, and no CI. Frame every issue around the
> framework's surfaces, not app features.

## Research first — think like an architect

A requested issue is **never** a quick transcription of the user's sentence. Before
writing a single line of the body, do a **thorough analysis** as if you were the
architect who'll own the work:

- **Read the actual framework code.** Trace the relevant skills/agents/hooks and
  the root policy files that govern them (`AGENTS.md`, `.claude/CLAUDE.md`,
  `CLAUDE.local.md`, `.claude/DOD.md`). The `## Proposed approach` must reference
  real files (`.claude/skills/<name>/SKILL.md`, `.claude/hooks/<name>.sh`,
  `.claude/agents/<name>-agent.md`) and fit the framework's conventions, not a
  generic guess.
- **Study best practices & the docs.** Check current Claude Code conventions and
  how any involved library actually wants this done — use the **`context7` skill**
  (Context7 CLI) for version-accurate API/config details rather than training memory.
- **Sketch a real solution.** The `## Proposed approach` should read like an
  architect's first pass: the chosen direction, the touched skills/agents/hooks/docs,
  the trade-offs and edge cases you noticed. It's a loose sketch, but an *informed* one.

**Run all of this in the background via subagents.** Every fan-out — codebase
exploration, doc lookups via the `context7` skill, best-practice research — goes to
an `Explore`/`general-purpose` (or relevant) subagent, ideally several in parallel,
not inline. Spawn them, let them dig, then synthesize their findings into the issue
body. Only the final write-up and `gh issue create` happen in the foreground.

## Conventions

### Title — type-prefixed, imperative, Sentence case (~50–70 chars)
Mirror the Conventional-Commit type. Lowercase prefix, no scope, no trailing period.

- `feat:` new capability · `fix:` (or `bug:`) something broken · `perf:` ·
  `refactor:` · `chore:`
- Good: `feat: add gh-issue helper skill` ·
  `fix: bash-validator false-positives on git push -f` ·
  `refactor: unify Next Steps source of truth across skills` ·
  `chore: de-duplicate skill listings in README`

### Labels — exactly two axes, one of each (seven total)
| Type | Area |
|---|---|
| `bug` · `feature` · `tech-debt` (covers refactor/perf/chore) | `skills` · `agents` · `hooks` · `docs` |

Pick one type + the area(s) it touches (usually one; multiple areas are fine for
cross-cutting work, e.g. a skill change that also needs a doc update →
`skills,docs`). **Don't** invent priority/status labels. If a label is missing,
create it (see cookbook) — colors: `bug d73a4a`, `feature 0e8a16`,
`tech-debt fbca04`, `skills 1d76db`, `agents 5319e7`, `hooks 5dc8cb`, `docs 0075ca`.

### Body — 3–4 sections, no boilerplate
English. Reference code by path (`.claude/skills/coder/SKILL.md`), link related
work by `#N`. Drop any section that adds nothing.

**Bug:**
```markdown
## Problem
What's wrong + where (skill/hook/agent). Expected vs actual, one line each.

## Repro (only if non-obvious)
1. …

## Notes
Suspected cause / file refs. Optional.
```

**Feature / tech-debt:**
```markdown
## Context
Why this matters / the current limitation.

## Proposed approach
Loose bullet sketch — it's a backlog item, not a spec.

## Acceptance criteria
- [ ] Observable outcome 1
- [ ] Observable outcome 2

## References
Code paths, related issues/PRs. Optional.
```

`## Acceptance criteria` is the one section worth never skipping for a task — it
prevents scope drift. Skip severity matrices, impact/effort scoring, repro % etc.

## Create it

```bash
gh issue create \
  --title "feat: add gh-issue helper skill" \
  --body-file body.md \           # or: --body-file - with a heredoc
  --label feature,skills
```
Returns the new issue URL; the number is the trailing path segment
(`n=${url##*/}`). Prefer `--body-file` over inline `-b` for multi-line Cyrillic
bodies — fewer quoting traps.

## Linking with PRs

- The **PR that implements an issue** carries `Closes #N` at the top of its body
  (the **`gh-pr` skill** does this) → the issue auto-closes on merge. Leave such an
  issue **open**; don't pre-close it.
- To cross-reference without closing, write a plain `Related to #N` / `Part of #N`
  in the body or a comment.

## Retroactive records (issue for already-merged work)

When migrating finished work into an issue (e.g. a backlog cleanup), create it
then close as **completed** with a comment pointing at the merged PR — typing
`#N` cross-links the two:

```bash
url=$(gh issue create -t "refactor: unify Next Steps source of truth" --body-file body.md -l tech-debt,skills)
gh issue close "${url##*/}" --reason completed --comment "Done in #6"
```

`--reason completed` for done work; `not planned` for won't-do/obsolete;
`duplicate` (with `--duplicate-of <n>`) for dupes.

## Cookbook

```bash
gh label create <name> --color <hex> --description "<desc>" -f   # -f upserts
gh issue list                          # open issues
gh issue list --state all --label bug
gh issue view <N>
gh issue edit <N> --body-file body.md  # fix a body / add labels (--add-label)
```

After creating, report the issue number + URL back to the user.
