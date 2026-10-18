---
name: architect
context: fork
agent: architect
description: System architecture decisions for Python projects. Use when designing new features, choosing patterns, evaluating scalability/security, or making technology choices. Triggers on "design", "architect", "should I use", "which pattern", "scalability", "security design".
phase: planning
flow-next: api-designer
flow-alternatives: [writing-plans, coder]
related: [brainstorm, api-designer, context7]
---

# Architect

## Task

$ARGUMENTS

Carry out the request above using the guidance in this skill. When finished, STOP and emit the Context Summary + Next Steps.

## Overview

Make system architecture decisions for Python projects following layered architecture principles (handler/route → service → data access). Framework-agnostic: applies to FastAPI, Django, Flask, or non-web Python. Only the outermost handler layer differs between frameworks.

> **Documentation lookup:** before finalizing framework-specific scalability/security patterns, consult [[/context7]] (`npx -y ctx7 …`) for the library's current, version-specific guidance rather than relying on memory.

## Generated File Naming Convention (MANDATORY)

**ANY file created by this skill MUST be prefixed with `architect-`.**
Predefined output (`architect-architecture.md`) already follows this convention.
Any additional ad-hoc files (summaries, notes, diagrams) MUST also follow this rule:
- ✅ `architect-decision-log.md`
- ❌ `DECISION_LOG.md`

## Decision Trees

### Where to Place New Code?

```
New functionality needed
        │
        ├── Business domain specific?
        │   └── YES → <package>/<feature>/
        │
        ├── Cross-cutting infrastructure?
        │   └── YES → <package>/core/ (or shared/)
        │
        └── External service integration?
            └── YES → <package>/integrations/ or feature-specific
```

### Which Pattern to Use?

```
Operation type?
        │
        ├── HTTP request handling?
        │   └── Route handler / view
        │
        ├── Business logic?
        │   └── Service
        │
        ├── Data access?
        │   └── Repository
        │
        └── Utility/helper function?
            └── Module-level function or shared helper
```

## Architecture Layers

```
┌─────────────────────────────────────────────────────────┐
│ Presentation Layer (route handlers/views, schemas)       │
├─────────────────────────────────────────────────────────┤
│ Service Layer (business logic)                           │
├─────────────────────────────────────────────────────────┤
│ Data Access Layer (repositories, models)                 │
└─────────────────────────────────────────────────────────┘
```

## Dependency Rules

```
✅ ALLOWED:
handler → service → repository
any layer → shared utilities

❌ NOT ALLOWED:
repository → service
service → handler
```

## Package Structure

```
<package>/<feature>/
├── __init__.py
├── api.py            # route handlers / views (framework entrypoint)
├── service.py
├── repository.py     # only if a database is used
├── schemas.py        # request/response models (Pydantic / dataclasses)
└── models.py         # ORM models / domain entities (only if DB is used)
```

Django projects map this onto an app (`models.py`, `views.py`, `serializers.py`). Keep the layering regardless of file layout.

## Key Decisions

### Model Relationships (SQLAlchemy example)

| Relationship | Implementation | When |
|--------------|---------------|------|
| One-to-One | `relationship(..., uselist=False)` + FK | Rarely, usually merge models |
| One-to-Many | `relationship()` + `ForeignKey` | Common parent-child |
| Many-to-Many | `relationship(secondary=...)` + association table | Tags, categories |

(Django ORM: `OneToOneField`, `ForeignKey`, `ManyToManyField`.)

### Transaction Boundaries

| Scenario | Transaction? | How |
|----------|-------------|-----|
| Single write | No (implicit) | Direct repository call |
| Multiple related writes | Yes | Service wraps work in one session/`begin()` block |
| Read operation | No | Repository call |

### Security Considerations

- [ ] Authentication required?
- [ ] Authorization rules?
- [ ] Input validation?
- [ ] Rate limiting?
- [ ] Audit logging?
- [ ] Data encryption?

### Scalability Considerations

- [ ] Database indexing strategy?
- [ ] Caching opportunities?
- [ ] Async processing needed?
- [ ] Horizontal scaling requirements?

## Quick Reference

| Decision | Answer |
|----------|--------|
| New business entity | Model + Repository + Service + handler |
| External API integration | Shared integration service |
| Shared utilities | `core/` (or `shared/`) package |
| Feature flag | Configuration / settings module |
| Background job | Task queue + worker (Celery, RQ, arq) |
| Real-time updates | WebSockets or SSE |

---

## Final Output (MANDATORY)

**Before presenting next steps, you MUST update the living specification:**

### Living Specification Update Process

1. **Read project context:**
   - Read `specs/MANIFEST.md` to understand the project overview
   - This is the entry point for all specifications

2. **Read existing architecture:**
   - If `specs/architect-architecture.md` exists, read it to understand existing architecture
   - If it doesn't exist, you'll create it using the structure from `../../../spec-desc.md`

3. **Get task number:**
   - If coming from previous skills, use the task number from context (e.g., "TASK-001")
   - Task number should be included in all section headers

4. **Update architect-architecture.md:**
   - **If file doesn't exist:** Create using the structure template from spec-desc.md
   - **If file exists:** Append new sections with `### [TASK-N] Feature Name` prefix
   - Include date in section: `### [TASK-001] User Authentication (2026-01-22)`
   - Document:
     - Module placement decisions
     - Pattern choices with rationale
     - Entity relationships
     - Security considerations
     - Scalability considerations
     - Key architectural decisions

5. **Update MANIFEST.md if needed:**
   - Update "Last Updated" date for architect-architecture.md entry
   - Add new modules to "Key Decisions" section if applicable
   - Update "Tech Stack" if new technologies are introduced

**Example section in architect-architecture.md:**
```markdown
### [TASK-001] User Authentication (2026-01-22)

**Package:** `app/auth/`

**Pattern:** Layered architecture with JWT tokens

**Models:**
- User (id, email, password_hash, created_at)

**Security:**
- bcrypt/argon2 for password hashing
- JWT with 15min expiry
- Refresh token rotation

**Scalability:**
- Stateless authentication
- Redis for token blacklist
```

This incremental update ensures living documentation that grows with the project.

---

## Next Steps

After updating specs/architect-architecture.md and specs/MANIFEST.md, STOP and present these options:

**Next by flow:** [[/api-designer]] `<TASK-{N}: one-line summary of what you did>` - Design REST APIs based on the architecture. See [[moc-planning]] for phase context.

**Pass to next skill:** Include the task number in your context summary (e.g., "TASK-001: Authentication architecture documented")

**Alternatives:**
- [[/writing-plans]] `<TASK-{N}: one-line summary of what you did>` - Create implementation plan if APIs are already defined.
- [[/coder]] `<TASK-{N}: one-line summary of what you did>` - Start implementation if architecture is simple and clear.
