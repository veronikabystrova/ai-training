# Task Documentation

This directory contains **temporary** documentation for specific implementation tasks — scratch for
*one* feature (its requirements, design, and plan). It is the per-feature counterpart to
[`specs/`](../specs/README.md), which is the project's **permanent** living memory. Rule of thumb:
delete `tasks/` after a merge; keep `specs/`.

## Structure

Each task has its own directory: `TASK-001/`, `TASK-002/`, etc.

Task directories may contain:
- `requirements-analyst-requirements.md` - Requirements analysis (from /requirements-analyst)
- `brainstorm-design.md` - Design decisions (from /brainstorm)
- `writing-plans-plan.md` - Implementation plan (from /writing-plans)

## Task Numbering

Tasks are auto-numbered starting from 001 (zero-padded to 3 digits). The `.task-counter` file tracks the next number.

## When you finish a feature → delete `tasks/TASK-N/`

Task directories are temporary scaffolding. Once the branch is merged, delete `tasks/TASK-N/`.
Concretely, delete after:
- Implementation is complete
- Code is merged/deployed
- Team has reviewed the work

Keep a task dir only if you still need to reference its original requirements or design decisions.
For *ongoing* documentation, you don't need `tasks/` at all — the living specifications in `specs/`
are the durable record (and they're **not** deleted). See [`specs/README.md`](../specs/README.md).

## Living Specifications

For ongoing project documentation, see `specs/`:
- `specs/MANIFEST.md` - Project overview and spec index
- `specs/architect-architecture.md` - System architecture
- `specs/api-designer-spec.md` - API endpoints and schemas
- `specs/docs-generator-implementation.md` - Build process, deployment, tooling
