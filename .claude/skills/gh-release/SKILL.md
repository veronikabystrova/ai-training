---
name: gh-release
description: Cut a release for the accelerator-mini-python repo — consolidate the CHANGELOG's [Unreleased] section into a dated version, tag it, and publish the GitHub release. Use whenever the user asks to release/ship a version, bump the version, or tag a release. Pairs with gh-pr (which accumulates [Unreleased] entries per PR). Activates on "сделай релиз", "выпусти версию", "зарелизь", "cut a release", "publish a release", "bump version", "tag a release".
---

# Cutting a release for accelerator-mini-python

The golden rule: **a release is the moment `[Unreleased]` becomes a version.** Each PR
already drops one terse line under `## [Unreleased]` in `CHANGELOG.md` (the **`gh-pr`
skill** enforces this). This skill owns the *other half* of that contract: rename
`[Unreleased]` → a dated `[X.Y.Z]` section, open a fresh empty `[Unreleased]`, tag, and
publish the GitHub release. This is the canonical [Keep a Changelog](https://keepachangelog.com/)
flow, not a workaround.

> This repo is **Accelerator Core** — a Claude Code *framework* (skills, agents, hooks,
> docs under `.claude/` + root policy files), not an application. **No CI** and **no
> version file** (`pyproject.toml`/`package.json`/`VERSION`) exist: the version lives in
> exactly three places — the `CHANGELOG.md` header, the **git tag**, and the **GitHub
> release**. There is nothing else to bump.

## Conventions (project-specific)

- **Branch from `main`.** The CHANGELOG edit lands via a branch + PR, never a direct push
  to `main` — even for a release.
- **English** for the tag, release title/notes, PR body, and commit message (chat stays Russian).
- **SemVer** version numbers, **`vX.Y.Z`** tag format (matches `v2.0.0`/`v2.1.0`/`v2.2.0`).
- Commit messages end with: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`
- PR bodies end with: `🤖 Generated with [Claude Code](https://claude.com/claude-code)`
- **Reuse the `gh-pr` skill for the PR itself** — branch/commit/push/PR-body/merge mechanics
  all follow `gh-pr`. This skill adds only the version + CHANGELOG + tag + release logic on top.

## Step 1 — Preconditions

```bash
git switch main && git pull --ff-only        # release from an up-to-date main
git status --porcelain                        # MUST be clean
git describe --tags --abbrev=0                # latest tag, e.g. v2.2.0
gh release list                               # cross-check published releases
```

Then read `CHANGELOG.md` and inspect `## [Unreleased]`:

- **Empty `[Unreleased]` → STOP.** There is nothing to release; tell the user and exit.
- Otherwise note which buckets are populated (`Added` / `Changed` / `Fixed` / `Removed`) —
  Step 2 needs them.

## Step 2 — Decide the version, then confirm

Infer the bump from the `[Unreleased]` buckets (SemVer):

| Signal in `[Unreleased]` | Bump |
|---|---|
| A breaking change or a **removed** public capability | **major** (`X+1.0.0`) |
| Any **Added** (new skill/agent/hook/capability) | **minor** (`X.Y+1.0`) |
| Only **Fixed** / non-breaking **Changed** | **patch** (`X.Y.Z+1`) |

Propose the resulting number, then **confirm with `AskUserQuestion`** (the user may
override — e.g. they want to call it a major). Never tag without that confirmation; the
version is a one-way, outward-facing decision.

## Step 3 — Consolidate the CHANGELOG

Edit `CHANGELOG.md`:

1. Rename the `## [Unreleased]` heading to `## [X.Y.Z] - YYYY-MM-DD` (today's date).
2. Insert a **fresh empty `## [Unreleased]`** above it (so the next PR has a home).
3. Leave every entry — including its `(shorthash)` suffix — exactly as written. Match the
   file's existing spacing/format precisely; do not rewrite prose.

Result shape:

```markdown
## [Unreleased]

## [X.Y.Z] - YYYY-MM-DD

### Added
- … (abc1234)
```

## Step 4 — Release PR (via the `gh-pr` skill)

Open the PR following the **`gh-pr`** conventions, with these release-specific values:

```bash
git switch -c chore/release-vX.Y.Z
git add CHANGELOG.md
git commit -m "$(cat <<'EOF'
chore(release): vX.Y.Z

Consolidate [Unreleased] into the vX.Y.Z section.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
git push -u origin chore/release-vX.Y.Z
gh pr create --title "chore(release): vX.Y.Z" --body "$(cat <<'EOF'
Cut release vX.Y.Z — consolidate the CHANGELOG [Unreleased] entries into a dated version section.

## Changes
- Move [Unreleased] → [X.Y.Z] - YYYY-MM-DD in CHANGELOG.md
- Open a fresh empty [Unreleased]

## Docs
- CHANGELOG.md: this PR *is* the changelog change (consolidation only — no new entry needed)
- No README/.claude docs change — release mechanics unchanged
- No policy change — AGENTS.md / .claude/CLAUDE.md untouched

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

A release PR usually has **no issue to close** — omit the `Closes #N` line. There is **no
CI** here (see `gh-pr` Step 4), so the merge gate is local sanity + user approval. Merge when
the user asks:

```bash
gh pr merge <N> --squash --delete-branch
```

## Step 5 — Tag and publish the GitHub release

After the PR merges, fast-forward local `main`, then create the release. `gh release create`
**creates the tag** on `--target`, so a separate `git tag` is not needed:

```bash
git switch main && git pull --ff-only

gh release create vX.Y.Z \
  --target main \
  --title "vX.Y.Z — <one-line theme>" \
  --notes "$(cat <<'EOF'
<notes — see below>
EOF
)"
```

**Build the notes from the just-consolidated `[X.Y.Z]` CHANGELOG section** (keep it DRY —
the changelog is the source of truth). Optionally lead with a one-paragraph summary, then the
bullet list, and **always** end with a compare-link footer matching the `v2.2.0` style:

```
**Full changelog:** https://github.com/<owner>/<repo>/compare/<prev-tag>...vX.Y.Z
```

Get `<owner>/<repo>` from `gh repo view --json nameWithOwner -q .nameWithOwner` and
`<prev-tag>` from Step 1.

## Step 6 — Report

Report back to the user:
- the new tag and the **release URL** (`gh release view vX.Y.Z --json url -q .url`),
- the CHANGELOG diff (the `[Unreleased]` → `[X.Y.Z]` move),
- confirmation that a fresh empty `[Unreleased]` is in place for the next cycle.

Tag and publish **only when the user has asked to release** — never cut a release as a
side effect of other work.
