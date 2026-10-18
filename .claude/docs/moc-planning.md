---
title: Planning Phase
type: moc
phase: 2
description: Design architecture, APIs, and create implementation plans.
---

# Planning Phase

The second phase turns requirements and designs from [[moc-understanding]] into actionable specifications. It updates living spec files in `specs/` and produces implementation plans in `tasks/TASK-{N}/`.

## Commands

[[/architect]] makes system architecture decisions — choosing patterns, evaluating scalability and security, and documenting the technical approach in `specs/architect-architecture.md`.

[[/api-designer]] designs REST APIs with proper conventions, request/response schemas, and OpenAPI documentation. Outputs to `specs/api-designer-spec.md`.

[[/writing-plans]] produces detailed implementation plans with bite-sized tasks for engineers with zero codebase context. Outputs to `tasks/TASK-{N}/writing-plans-plan.md`.

## Flow

The default path is [[/architect]] then [[/api-designer]] then [[/writing-plans]]. Steps can be skipped based on project needs — a simple feature may go directly from [[/architect]] to [[/writing-plans]].

## Outputs

- `specs/architect-architecture.md` — architecture decisions and patterns
- `specs/api-designer-spec.md` — REST API specification with request/response schemas
- `tasks/TASK-{N}/writing-plans-plan.md` — implementation plans with task breakdowns

## Next Phase

Continue to [[moc-execution]] to create workspaces and implement the plan.
