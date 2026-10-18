# Living Specifications

This directory is the **project's durable memory** — the living specification of the *system*.
Unlike `tasks/` (temporary, per-feature scratch that you delete after a merge), `specs/` is
**permanent** and grows with the project.

## Why this exists

When a skill runs, it forks into an isolated agent with no memory of past work. `specs/` is how that
agent gets context **without re-deriving it from scratch**: it reads `MANIFEST.md` first to see what
the project is and which spec files exist, then opens only the file relevant to its job. That's why
specs are kept, not reset per feature — deleting them makes every future agent relearn the system.

## Structure

```
specs/
├── MANIFEST.md                        # Index + project overview — always read first
├── architect-architecture.md          # System design, components, data flow
├── api-designer-spec.md               # Endpoints, schemas, authentication
└── docs-generator-implementation.md   # Build process, deployment, tooling
```

`MANIFEST.md` is the entry point: a 2–3 sentence project purpose, a table indexing the spec files
(purpose + dependencies + last-updated), and the key cross-cutting decisions. Agents load it first,
then follow it to the one focused file they need — see `../spec-desc.md` for the full structure
template and the agent-specific loading pattern.

## Who writes it, and how

These skills update specs as part of the normal flow:

| Skill | Updates |
|-------|---------|
| `architect` | `architect-architecture.md` + Key Decisions in `MANIFEST.md` |
| `api-designer` | `api-designer-spec.md` (endpoints, schemas) |
| `docs-generator` | `docs-generator-implementation.md` |

**Update pattern:** new work is appended as `### [TASK-N] …` sections, and the skill bumps the
"Last Updated" entry for that file in `MANIFEST.md`. The `[TASK-N]` tag records *which* task
introduced the change.

## Lifecycle — do NOT delete

`specs/` is permanent. When you finish a feature you delete `tasks/TASK-N/`, but you **keep** the
specs — they're the context the next feature relies on. The only per-feature obligation is to make
sure the specs reflect any architecture or API change you made (the skills above handle this during
the flow, and `/verify`'s Full tier checks "Specs updated if architecture/API changed").

## Managing growth (won't this bloat?)

Specs grow by design — that's what "living" means. Two things keep it from becoming a problem:

1. **Modular files + the `MANIFEST.md` index** mean an agent reads only the one relevant file, never
   the whole folder. Growth doesn't inflate per-task context cost.
2. **`[TASK-N]` is a provenance marker, not an append-only log you must keep forever.** When a file
   gets unwieldy, consolidate superseded sections by hand (or re-run `/docs-generator`) so the spec
   describes the *current* system, not its entire history.

## See also

- [`MANIFEST.md`](MANIFEST.md) — the project index (start here)
- [`../spec-desc.md`](../spec-desc.md) — specification structure template & loading pattern
- [`../tasks/README.md`](../tasks/README.md) — the temporary, per-feature counterpart to this folder
