---
name: gh-pr
description: Open a pull request for the accelerator-mini-python repo. Use whenever the user asks to create/open/raise a PR, or whenever you open one yourself. Ensures every PR also keeps the framework docs in sync — if the change altered how a skill/agent/hook behaves or how the framework is used, the docs (including CHANGELOG.md) are updated in the same PR. Activates on "создай PR", "открой пул-реквест", "сделай PR", "open a PR", "raise a pull request".
---

# Opening a pull request for accelerator-mini-python

The golden rule: **a PR is never just code.** Before opening one, check whether
the change makes the framework docs stale, and if so update them **in the same
branch/PR**. Shipping a behaviour change with stale usage/setup docs is the exact
gap this skill closes.

> This repo is **Accelerator Core** — a Claude Code *framework* (skills, agents,
> hooks, docs under `.claude/` + root policy files), not an application. There is
> no app source tree, no frontend, and **no CI** (see Step 4).

## Conventions (project-specific)

- **Branch from `main`.** Never push docs/code straight to `main` — always a
  branch + PR, even for one-file changes.
- **English** for PR titles, PR bodies, and commit messages (chat stays Russian).
- Commit messages end with:
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`
- PR bodies end with:
  `🤖 Generated with [Claude Code](https://claude.com/claude-code)`
- Branch naming: `<type>/<short-slug>` (`feat/`, `fix/`, `docs/`, `chore/`,
  `refactor/`). Mirror the conventional-commit type of the work.
- **Link the issue the PR resolves.** Most PRs implement a tracked issue — put a
  `Closes #N` line at the **top of the PR body** so it auto-closes on merge (see
  Step 3). If no issue exists yet for non-trivial work, create one first with the
  **`gh-issue` skill**, then link it. Pure chores/dependency bumps may have no issue —
  that's fine: **just omit the issue line entirely.** Don't add a "No issue —…"
  note; an absent line is the cleanest signal. Never invent or guess an issue number.

## Step 1 — Sync the docs (do this BEFORE committing)

Decide whether this change needs a **docs** update (and, separately, a **policy/memory**
update — see the subsection below). **A docs update is needed** when the diff touches
any of:

- **How a skill/agent/hook is used or behaves** — a new/renamed/removed skill, a
  changed trigger or flow step, a hook that blocks something new.
- **Framework usage / install** — the copy set, onboarding steps, the skill flow,
  available commands.
- **Architecture / structure** — the phase model, the docs system, agent/skill
  layout, naming conventions.

It usually does **not** for: pure internal wording tweaks, a bugfix with no
behaviour change, or a change a reader would never observe. When unsure, skim
`README.md` and the relevant `.claude/docs/` file and ask yourself "would this now
mislead a reader?" — if yes, update it.

The framework's human-facing docs are:
- `README.md` (root) — framework overview, install/copy set, working-a-ticket flow.
- `.claude/README.md` — the in-`.claude` human reference.
- `.claude/docs/` — navigation docs: `SKILL-FLOW.md` (master flow) and `moc-*.md`
  (Maps of Content, one per phase). Update these when you add/rename/remove a *flow*
  skill or change the flow edges. (Helper skills like `gh-issue`/`gh-pr` are
  intentionally **not** in the flow — don't add them here.)

Read the relevant file, edit only the lines that went stale (don't rewrite
wholesale), and keep the existing terse, English, command-driven tone.

### Always log the change in `CHANGELOG.md`

`CHANGELOG.md` (root) follows [Keep a Changelog](https://keepachangelog.com/) +
SemVer. **Every PR that ships an observable change adds one line under the
`## [Unreleased]` section** (create that section just under the format preamble
if it's missing), in the right bucket — `Added` / `Changed` / `Fixed` /
`Removed`. Keep it to a **single terse line, no details** — the "what & why" and
the per-change bullets live in the PR's `## Changes` section (Step 3), so don't
duplicate them here. Append the PR's short commit hash in parentheses once known,
matching the existing entries (e.g. `- Add X helper skill (abc1234)`). Pure
no-op chores a reader would never observe (e.g. a typo in a comment) may skip it;
when in doubt, add the line.

### Also sync the policy/memory files when the contributor model changes

`AGENTS.md` (enforceable policy, single source of truth) and `.claude/CLAUDE.md`
(project memory imported at session start) steer every future session — keep them
accurate **in the same PR** whenever the change alters something they document:
policy rules, the phase model, toolchain, agent behaviour, file-naming, or the DoD.
`CLAUDE.local.md` is the gitignored maintainer note — update it too if a "gotcha"
or the repo map changed, but it never ships. Edit only the stale lines, matching
each file's style. Changes they don't describe need no edit.

> **You MUST report any edit to `AGENTS.md` or `.claude/CLAUDE.md` explicitly** in
> the final message to the user — call it out separately from the docs note (see
> Step 4). They steer every future session, so the user has to know they moved. If
> the change touches policy/architecture but you decide they need no edit, say so
> and why.

### When README.md starts to bloat → push detail into `.claude/docs/`

If a section grows long enough that `README.md` becomes hard to scan (rule of
thumb: a single topic needs more than ~30–40 lines, or you'd be adding a third
deep subsection), **don't keep growing the README**. Instead move the detail into
the matching navigation doc under `.claude/docs/` (e.g. a flow concept → a `moc-*`
file or `SKILL-FLOW.md`), leave a 1–3 line summary in `README.md`, and **link it**
so nothing becomes orphaned. The README should stay a launchpad, not the whole
manual.

## Step 2 — Branch, commit, push

```bash
git checkout -b <type>/<slug>
git add <files>                # code + README.md / .claude/docs/* / CHANGELOG.md / AGENTS.md / .claude/CLAUDE.md together
git commit -m "$(cat <<'EOF'
<type>: <imperative summary>

<why / what changed, wrapped ~72 cols>

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
git push -u origin <type>/<slug>
```

## Step 3 — Open the PR

```bash
gh pr create --title "<type>: <summary>" --body "$(cat <<'EOF'
Closes #<issue>          # ← include ONLY if a real issue exists; otherwise drop this line + the blank line after it

<1–2 sentence what & why>

## Changes
- <bullet per meaningful change>

## Docs
- <what README/.claude docs were updated, or "No docs change needed — <reason>">
- <`CHANGELOG.md`: logged under [Unreleased], or "no changelog entry — <reason>">
- <`AGENTS.md` / `.claude/CLAUDE.md`: what was updated, or "no policy change — <reason>">

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Always include the **Docs** section in the body — it forces the explicit
decision and tells the reviewer the docs were considered. List README/`.claude/docs`,
the `CHANGELOG.md` entry, **and** the policy/memory files (`AGENTS.md`,
`.claude/CLAUDE.md`) separately; if any needed no change, say so and why.

**Closing keywords** (`Closes`/`Fixes`/`Resolves #N`, case-insensitive) only
auto-close when they're in the **PR body or a commit message** (not a PR comment)
and the PR targets **`main`** — both true here. One keyword per issue:
`Closes #1, closes #2` (a bare comma-list after one keyword only links the first).
To reference without closing, write a plain `Related to #N` / `Part of #N`. If
there's genuinely no issue, **omit the line entirely** — don't leave a `No issue`
placeholder; the absent line is the clean signal.

## Step 4 — Verify, then report

**This repo currently has no CI** (`.github/workflows/` is absent) and ships no
test/lint tooling of its own. So there are no remote checks to watch — don't claim
a green CI that doesn't exist. Before reporting:

```bash
ls .github/workflows/ 2>/dev/null   # detect CI
```

- **If CI exists** (a workflow was added later): wait for it —
  `gh pr checks <N> --watch --interval 20` (blocks until checks conclude). If red,
  open the failing job (`gh run view <run-id> --log-failed`), report what broke,
  and fix it before any merge.
- **If CI is absent** (current state): say so plainly, and instead run whatever
  local verification the diff warrants — e.g. confirm changed hooks still parse
  (`bash -n .claude/hooks/<name>.sh`), skill frontmatter is valid, links resolve.
  If a Python toolchain (`uv`/`ruff`/`pytest`/`mypy`) is present, run it; if not,
  state that there's nothing to run and why.

Report the PR URL **and the verification result** back to the user. **If you edited
`AGENTS.md` or `.claude/CLAUDE.md`, say so explicitly in this report** (a one-line
"Updated AGENTS.md: …" is enough) — never let a policy/memory change ride along
silently. Only merge if the user asks (here or already in this session) — see below.

## Merging (only when asked)

```bash
# If CI exists, gate on it first:
gh pr checks <N> --watch --interval 20   # must be all green before merging
gh pr merge <N> --squash --delete-branch
```

- With **no CI**, the gate is simply: local verification passed **and** the user
  asked to merge. Never merge work you haven't verified.
- Squash is the repo default (history is PR-per-commit on `main`).
- After merging, the local `main` is fast-forwarded and the branch is deleted.
  Confirm the merged state (`gh pr view <N> --json state,mergedAt`) before
  reporting "merged".
