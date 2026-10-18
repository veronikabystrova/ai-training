# Project: Trainer Platform Backend (python-accelerator)

Multi-tenant backend for independent sports trainers, their coaches and their players (including
parent-managed child accounts). Each trainer is a tenant. One FastAPI service plus an ARQ worker
provide identity, tenancy, scheduling and commerce features, delivered epic by epic. TASK-001 builds
the Phase 0 foundation and Epic-01 (identity and tenancy), which every later epic depends on.

## Specs Index

| File | Purpose | Depends On | Last Updated |
|------|---------|------------|--------------|
| architect-architecture.md | System design, components, data flow | - | 2026-10-18 (TASK-001) |
| api-designer-spec.md | Endpoints, schemas, authentication | architect-architecture | 2026-10-18 (TASK-001) |
| docs-generator-implementation.md | Build process, deployment, tooling | - | - |

## Key Decisions

- Layering: horizontal `api/v1 → services → repositories → models`; services own transactions (A-01)
- Identity/tenant contract: `api/deps.py` (`Principal`, `TenantScope`, `current_principal`, `require_role`,
  `require_tenant_scope`, `player_context`, `SuperAdminScope`). Every epic consumes it; signatures are a cross-epic API
- Tenant source: trainer ⇒ self; coach ⇒ active `coach_profiles` row; player ⇒ `?trainer_id=` validated per request (A-02)
- Principal re-validated against the DB on every request, so revocation is immediate (A-03)
- Cross-tenant access ⇒ 404; tenant-scoped repositories require `trainer_id`
- `coach_profiles`: surrogate PK, one row per tenure, partial UQ `uq_coach_one_active_trainer` (A-04)
- `one_time_tokens`: single table, `purpose` enum, SHA-256 hash, atomic single-use consume (A-05)
- Cross-epic dependencies via `typing.Protocol` seams + fakes in `services/ports.py` (A-06)
- IDs: app-generated UUIDv7; all FKs `ON DELETE RESTRICT`; IntegrityError mapped by constraint name
- Auth: argon2id, HS256 access JWT 15 min, rotating opaque refresh 30 d with reuse detection
- API: `/api/v1`, domains auth/me/trainer/public/admin; error envelope `{"error":{code,message,details}}`; cursor `Page[T]`;
  `extra="forbid"` on requests; `Idempotency-Key` on approvals; one-time-token failures collapse to 400 `TOKEN_INVALID`;
  player tenant via `?trainer_id=` only on tenant-bound routes (`/me/approvals`, `/me/contexts` lists values)

## Tech Stack

- Python 3.12, uv, ruff, mypy (strict), pytest + pytest-asyncio, testcontainers
- FastAPI, Pydantic v2 + pydantic-settings, SQLAlchemy 2.x async + asyncpg, Alembic
- PostgreSQL 16 (`citext`, `btree_gist`, `pg_trgm`), Redis 7, S3-compatible object storage (boto3)
- ARQ (background jobs + cron), structlog
- Security libs: argon2-cffi, pyjwt, zxcvbn, defusedxml; rate limiting: limits; UUIDv7: uuid-utils

---

*This manifest is updated automatically by architect, api-designer, and docs-generator skills.*
*See `../spec-desc.md` for specification structure guidelines.*
