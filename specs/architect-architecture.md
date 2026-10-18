# Architecture

> Living spec. Read `MANIFEST.md` first. New work is appended as `### [TASK-N] …` sections.
> Endpoint shapes live in `api-designer-spec.md`. This file holds structure, contracts, data model and decisions.

## System Overview

**Component:** one FastAPI service (Python 3.12, async) + one ARQ worker process, sharing one codebase.
**Stores:** PostgreSQL 16 (system of record), Redis 7 (rate limits, ARQ queue), S3-compatible object storage (avatars, logos).
**Layering (enforced, Conv §2):** horizontal layers, not vertical feature packages.

```
HTTP ─► api/v1/<domain>/routes.py ─► services/*.py ─► repositories/*.py ─► models/*.py ─► Postgres
            │  schemas.py (Pydantic, extra="forbid")      │ Protocol ports (core/*)
            └─ api/deps.py (principal, tenant, rate limit, idempotency)
jobs/*.py (ARQ) ─► services/*.py   (same rules as routes: jobs never touch repositories directly)
```

| Rule | Allowed | Forbidden |
|---|---|---|
| Call direction | routes → services → repositories; anything → `core/` | repository → service, service → routes, routes → repositories |
| Transactions | service owns the unit of work (`async with session.begin()`) | repositories calling `commit()` |
| Time | inject `Clock` | `datetime.now()` in domain code |
| Tenant | from `Principal` / `TenantScope` | `trainer_id` read from a request body |
| Third-party libs | wrapped in `core/` behind a Protocol or a thin module | services importing `argon2`, `jwt`, `limits`, `boto3`, `zxcvbn`, `defusedxml` directly |
| Cross-epic data | `typing.Protocol` seam + fake | importing another epic's repository |

---

### [TASK-001] Phase 0 Foundation + Epic-01 Identity & Tenancy (2026-10-18)

**Source design:** `tasks/TASK-001/brainstorm-design.md` (flows, API additions, error codes, test strategy).
**Requirements:** `tasks/TASK-001/requirements-analyst-requirements.md` (FR/NFR/G-xx ids).
This section records the decisions that outlive the task. Detail that only matters during implementation stays in the task docs.

#### 1. Module placement

```
app/
  main.py                    app factory: routers, middleware, exception handlers
  core/                      cross-cutting, framework-light, no DB models
    config.py                pydantic-settings (JWT key, TTLs, argon2 params, retention, rate limits)
    errors.py                DomainError hierarchy + one FastAPI handler → {"error":{code,message,details}}
    pagination.py            opaque cursor encode/decode, Page[T] envelope
    clock.py                 Clock Protocol, SystemClock
    ids.py                   new_id() -> uuid.UUID (UUIDv7, uuid-utils)
    logging.py               structlog JSON, correlation id, PII redaction processor
    security.py              PasswordHasher, PasswordPolicy, TokenIssuer, hash_token()
    ratelimit.py             RateLimiter (limits.aio wrapper), RouteClass enum
    idempotency.py           request hashing + replay helpers
    email.py · storage.py    EmailSender / ObjectStorage Protocols + adapters + fakes
    svg.py                   sanitize_svg(bytes) -> bytes (defusedxml + allowlist)
  db/        session.py · base.py (Base, UUIDv7 PK mixin, timestamps) · alembic/
  models/    identity.py · admin.py (audit_log, feature_flags) · common.py (idempotency_keys)
  repositories/  one module per aggregate (user, profile, coach_profile, player, trainer_player_link,
                 share_link, availability, approval, impersonation, refresh_token, one_time_token,
                 erasure, audit, idempotency)
  services/  auth · user · player · sharelink · availability · approval · impersonation · branding
             audit (stub) · feature_flag (stub) · ports.py (cross-epic Protocols)
  api/       deps.py · middleware.py · v1/{auth,me,trainer,public,admin}/{routes,schemas}.py
  jobs/      worker.py (ARQ WorkerSettings, retry/backoff, dead-letter, cron) · identity_jobs.py
```

| Placement decision | Rationale |
|---|---|
| `PlayerService` split from `UserService` | Dependents, child login, trainer links and contexts form one aggregate with its own invariants; keeps `UserService` focused on account lifecycle |
| `AuditService`, `FeatureFlagService` built here as Protocol + stub | Every Epic-01 service needs them (Plan §1); Epic-07 swaps the implementation, not the call sites |
| Cross-epic seams in `services/ports.py` | `CommitmentChecker`, `PurchaseExecutor`, `CampSubmissionReader` are consumed by Epic-01 and implemented by Epics 02/05. One file makes the dependency surface visible |
| Security primitives in `core/security.py`, not a service | Stateless, no DB, used by services and `deps.py` alike |

#### 2. `api/deps.py`: the principal / tenant contract (consumed by every epic)

This is the most load-bearing interface in the system. Epics 02–08 MUST obtain identity and tenant
only through these dependencies. Changing a signature here is a breaking change for every epic.

```python
class Role(StrEnum): super_admin, trainer, coach, player

@dataclass(frozen=True, slots=True)
class Principal:
    user_id: UUID
    role: Role
    trainer_id: UUID | None   # trainer ⇒ self; coach ⇒ active coach_profiles.trainer_id; player/super_admin ⇒ None
    act: UUID | None          # impersonating super_admin's user_id, else None
    child: bool               # True iff a dependent player_profiles.login_user_id = user_id
    jti: UUID

@dataclass(frozen=True, slots=True)
class TenantScope:
    trainer_id: UUID                          # the only tenant id services may use
    principal: Principal
    player_profile_ids: frozenset[UUID]       # player callers: profiles they may act for under this trainer
                                              # (own + dependents, or only own for a child); empty for staff
```

| Dependency | Signature (FastAPI `Annotated` alias) | Behaviour | Failure |
|---|---|---|---|
| `get_session` | `SessionDep = Annotated[AsyncSession, Depends(get_session)]` | One `AsyncSession` per request; services open the transaction | — |
| `current_principal` | `PrincipalDep` | Decode JWT (HS256, `exp`, `typ=access`). Then **one SQL round trip** (see below) re-validates against the DB | 401 `INVALID_TOKEN` / `IMPERSONATION_EXPIRED`; 404 if trainer tenant deactivated (G-15) |
| `current_principal_any_tenure` | `AnyTenurePrincipalDep` | Same as `current_principal`, but a coach whose tenure is inactive is accepted (`trainer_id=None`) instead of 401. Used **only** on `POST /auth/register` for the authenticated coach move (user decision 2026-10-18) | as `current_principal` otherwise |
| `optional_principal` | `OptionalPrincipalDep` | Same, but `None` when no `Authorization` header (public routes that personalise) | as above if a header is present |
| `require_role(*roles)` | `Depends(require_role(Role.trainer))` → `Principal` | Role gate only, no tenant logic | 403 `FORBIDDEN` |
| `require_tenant_scope` | `TenantDep` | trainer/coach ⇒ `TenantScope(principal.trainer_id)`. player ⇒ delegates to `player_context`. super_admin ⇒ 403 (must use `SuperAdminScope`) | 403 / 404 |
| `player_context` | `PlayerContextDep` (reads `trainer_id: UUID = Query()`) | Requires an active `trainer_player_links` row for a profile the caller owns (child: only its own profile) and an active trainer. Builds `player_profile_ids` | 404 `NOT_FOUND` (never 403: no tenant oracle) |
| `SuperAdminScope` | `Annotated[Principal, Depends(require_super_admin)]` | Explicit, grep-able bypass marker. Rejects `act is not None` (impersonating admins cannot reach `/admin/*`, US-01.07) | 403 |
| `idempotency` | `IdempotencyDep` | Reads `Idempotency-Key`, scoped by `principal.user_id`; replays stored response; different hash ⇒ 409 | 409 `IDEMPOTENCY_KEY_REUSED` |
| `rate_limit(route_class)` | `Depends(rate_limit(RouteClass.login))` | Keyed per route class by IP / email / principal (limits in brainstorm design). The `default` class keys by principal, or by client IP when the route has no principal (e.g. `/auth/refresh`) (user decision 2026-10-18). **Redis outage: fail-open** on every route class (user decision 2026-10-18): the request proceeds unthrottled and a `rate_limit_unavailable` warning is logged. **Client IP = the socket peer address** (`request.client.host`); no proxy in front of the app for now, so `X-Forwarded-For` is ignored (user decision 2026-10-18; revisit with trusted-proxy config when a load balancer is added) | 429 + `Retry-After` |

**`current_principal` revalidation query** (single statement, indexed lookups only, keeps `GET /me` p95 < 100 ms):

```
users u (by sub)                              → status must be 'active' (pending trainers never hold a token)
LEFT JOIN coach_profiles cp ON cp.user_id=u.id AND cp.is_active     (coach only)
LEFT JOIN users t ON t.id = coalesce(<token trainer_id>, cp.trainer_id)  → tenant trainer status
LEFT JOIN impersonation_sessions s ON s.jti = <token jti> AND <act present>  → ended_at IS NULL AND expires_at > now
LEFT JOIN users a ON a.id = s.admin_user_id                                 → impersonating admin status must be 'active'
```

- The DB is authoritative; token claims are hints. A coach whose active `trainer_id` no longer matches the
  token claim gets 401 `INVALID_TOKEN`, so the client refreshes and the new token carries the new tenant.
  Deactivation, coach moves and impersonation exit therefore take effect on the next request, not after 15 min.
- An impersonated request (`act` present) also needs the impersonating super_admin to be `active`; otherwise
  401 `IMPERSONATION_EXPIRED`. Deactivating an admin ends their impersonation on the next request instead of
  at session expiry (user decision 2026-10-18).
- `child` is computed at token issue. Revoking a child login sets that user `inactive`, which the status check catches.

**Repository contract for tenant-scoped tables (FR-022):** methods take `trainer_id: UUID` as a required
keyword-only argument and always filter on it (directly or via `trainer_player_links`). A bare `get(id)`
does not exist on these repositories. A missing row and another tenant's row both yield `None` → service raises `NotFoundError`.

**Tenancy exceptions (G-10):** `player_profiles` and `availability_windows` carry no `trainer_id`; a player
belongs to many tenants. Access goes through `trainer_player_links (trainer_id, player_profile_id, status='active')`.
Every repository read of these tables from a staff context MUST join that link table.

#### 3. Data model decisions

Base schema: Epic-01 §7. Global rules:

| Rule | Decision |
|---|---|
| Primary keys | UUIDv7 generated in the app (`core.ids.new_id`), stdlib `uuid.UUID` type in Python, `uuid` in Postgres |
| Foreign keys | all `ON DELETE RESTRICT`; erasure anonymises rows instead of deleting users |
| Types | native Postgres enums, `citext` for emails, `timestamptz` for instants, `jsonb` for open structures |
| Constraint names | explicit, stable names; `IntegrityError` is mapped to domain errors **by constraint name** in one place (`core/errors.py`), never by pre-check SELECT |
| Extensions (baseline migration) | `citext`, `btree_gist`, `pg_trgm`; `CREATE TYPE timerange AS RANGE (subtype = time)` |

##### 3.1 `coach_profiles`: surrogate PK, one row per tenure (G-01)

```
coach_profiles
  id              uuid PK                    -- UUIDv7, new
  user_id         uuid NOT NULL FK users(id) RESTRICT
  trainer_id      uuid NOT NULL FK trainer_profiles(user_id) RESTRICT
  bio, credentials text NULL; certifications jsonb NOT NULL default '[]'; is_public bool NOT NULL default false
  joined_at       timestamptz NOT NULL
  left_at         timestamptz NULL           -- set when the row is deactivated
  is_active       bool NOT NULL default true
  CONSTRAINT ck_coach_left_iff_inactive CHECK (is_active = (left_at IS NULL))
  UNIQUE INDEX uq_coach_one_active_trainer ON (user_id) WHERE is_active
  INDEX ix_coach_trainer_active ON (trainer_id) WHERE is_active
```

| Decision | Rationale |
|---|---|
| Row = one coach-at-trainer **tenure**; `trainer_id` is immutable on a row | A move is "close old row, open new row" in one transaction. History stays queryable for audit and Epic-02 session history |
| Partial unique index is the concurrency guard | Two concurrent moves cannot both succeed; the loser's `IntegrityError` on `uq_coach_one_active_trainer` maps to `409 COACH_ALREADY_ASSIGNED`. No `SELECT … FOR UPDATE` on the coach needed |
| Downstream epics reference the coach by **`users.id` + `trainer_id`**, not `coach_profiles.id` | Coach identity is the user; the tenure row is an implementation detail. Validation helper: `CoachProfileRepository.get_active(*, trainer_id, user_id)` |
| On a move, public-profile fields are copied from the previous row | The coach's bio/credentials follow the person, not the tenant |
| API path ids `/trainer/coaches/{id}` use the coach `user_id` | Consistent with the downstream reference rule |

##### 3.2 `one_time_tokens` (G-02, new table)

```
one_time_tokens
  id          uuid PK
  user_id     uuid NOT NULL FK users(id) RESTRICT
  purpose     ott_purpose NOT NULL      -- enum: trainer_setup | password_reset | email_verify | email_change
  token_hash  bytea NOT NULL            -- SHA-256 of the raw token (32 bytes)
  payload     jsonb NULL                -- email_change only: {"new_email": ...}
  expires_at  timestamptz NOT NULL
  used_at     timestamptz NULL          -- consumed OR superseded
  created_at  timestamptz NOT NULL default now()
  UNIQUE INDEX uq_ott_token_hash ON (token_hash)
  INDEX ix_ott_user_purpose_open ON (user_id, purpose) WHERE used_at IS NULL
  INDEX ix_ott_expires ON (expires_at)  -- purge job
```

| Decision | Rationale |
|---|---|
| Raw token = `secrets.token_urlsafe(32)` (256 bits), stored as unsalted SHA-256 | High-entropy secret ⇒ a fast hash is sufficient and allows indexed lookup; a DB leak exposes no usable token. Same scheme as `refresh_tokens` (shared `core.security.hash_token`) |
| One table with a `purpose` enum instead of four tables | Identical lifecycle (issue, single-use consume, expire, purge); purpose is always part of the lookup so a token for one purpose can never be redeemed for another |
| TTL per purpose from config: `trainer_setup` 24 h, `password_reset` 1 h, `email_verify` 24 h, `email_change` 24 h | Spec values; `email_change` follows `email_verify` |
| Issuing a token supersedes open tokens of the same `(user_id, purpose)` (`UPDATE … SET used_at = now()` in the same transaction) | Only the newest link works; limits the window of old leaked emails |
| Consumption is a single atomic statement: `UPDATE … SET used_at = :now WHERE token_hash = :h AND purpose = :p AND used_at IS NULL AND expires_at > :now RETURNING user_id, payload` | Race-free single use without explicit locks; 0 rows ⇒ `400 TOKEN_INVALID` (one code for unknown/expired/used: no oracle) |
| Consume and its side effect (set password, activate user, swap email, revoke refresh families) share one transaction | A failed side effect (e.g. `uq_users_email` on email change ⇒ `409 EMAIL_TAKEN`) rolls the consumption back |
| Raw token leaves the process only via the email job payload; the job stores `token_id`, re-reads state, and is idempotent on it | Tokens never appear in logs (redaction by key name) or in the DB |
| Erasure deletes the user's rows; `purge_expired_tokens` (nightly) deletes rows expired or used > 7 days ago | GDPR (G-18) and bounded table size |

##### 3.3 Other schema deltas (summary; detail in the brainstorm design)

| Table | Decision | Gap |
|---|---|---|
| `player_profiles` | `owner_user_id NOT NULL`; age rule (`1 <= age < 18`, spec §10; user decision 2026-10-18) enforced in `PlayerService` with `Clock`, not a CHECK | G-07, G-08 |
| `availability_windows` | GiST `EXCLUDE` on `(subject_type, subject_id, weekday, timerange(start_time, end_time, '[)'))`; constraint name maps to `OVERLAPPING_WINDOWS` | G-09 |
| `users`, `profiles` | `pg_trgm` GIN indexes for the admin directory `query` filter | G-25 |
| `idempotency_keys` | `UNIQUE (key, principal_id)`, `expires_at` = +24 h, purged nightly | G-24 |
| `audit_log`, `feature_flags` | Minimal stubs owned by Epic-07; `audit_log` never stores emails | Plan §1, G-18 |

**Child account (G-04, G-06):** a child is a `users(role=player)` row referenced from a dependent
`player_profiles.login_user_id`. No new role and no new column; `Principal.child` is derived at token issue.

**User status machine (G-13):** `pending → active` only via `trainer_setup` token consumption;
`active ⇄ inactive` via admin; `→ deleted` is terminal (erasure). Only `active` users authenticate.

#### 4. Library decisions

All wrapped in `core/` so a replacement touches one module. Versions verified on PyPI 2026-10-18.

| Need | Library (min version) | Wrapped by | Rationale / risk |
|---|---|---|---|
| UUIDv7 (G-11) | `uuid-utils>=1.0` | `core.ids.new_id()` using `uuid_utils.compat.uuid7()` | RFC 9562, Rust-backed, returns stdlib `uuid.UUID` (SQLAlchemy/Pydantic compatible). Drop when the runtime moves to Python 3.14 (`uuid.uuid7`) |
| Password hashing | `argon2-cffi>=25.1` | `core.security.PasswordHasher` | Spec-mandated; argon2id `t=3, m=64 MiB, p=4`; `check_needs_rehash` on login enables parameter upgrades |
| Password strength (G-16) | `zxcvbn>=4.5` (score ≥ 3) + length ≥ 12 | `core.security.PasswordPolicy` (Protocol) | User decision: no breached-password check. Risk: the Python port is low-activity; the Protocol lets us swap in an alternative without touching services |
| JWT | `pyjwt>=2.15` | `core.security.TokenIssuer` | HS256, explicit `algorithms=["HS256"]` on decode, required claims `sub, role, exp, jti, typ`. `kid` rotation post-MVP |
| Rate limiting | `limits>=5.8` (`limits.aio`, Redis storage, `FixedWindowRateLimiter`) | `core.ratelimit.RateLimiter` + `api.deps.rate_limit` | Mature, async, storage-agnostic. Our wrapper owns keys, route classes and `429 + Retry-After` (from window stats) |
| SVG sanitising (G-19) | `defusedxml>=0.7.1` + own allowlist | `core.svg.sanitize_svg` | No maintained Python SVG sanitiser verified to strip `foreignObject`; `bleach` is HTML-only and deprecated. defusedxml blocks XXE and entity bombs; output is re-serialised from the allowlisted tree only |
| Fixed by the spec | fastapi, pydantic[email], pydantic-settings, sqlalchemy[asyncio] 2.x, asyncpg, alembic, redis, arq, boto3, python-multipart, structlog | — | Epic-01 §5, Conv §1 |

#### 5. Security

- **AuthN:** argon2id with dummy-hash verification for unknown emails (constant time). Access JWT 15 min;
  opaque refresh 30 d, rotating, reuse ⇒ family revoked. Refresh and one-time tokens stored only as SHA-256.
- **AuthZ:** role gates in `deps.py`; object-level checks in services; cross-tenant ⇒ 404. `trainer_id`
  only from `Principal`/`TenantScope`. `/admin/*` requires `SuperAdminScope` and rejects impersonation.
- **Impersonation:** never targets a super_admin; ≤ 60 min; refresh bounded by session `expires_at` (G-14); the refresh family is linked to its session by convention
  `refresh_tokens.family_id == impersonation_sessions.jti` (no FK column; user decision 2026-10-18);
  middleware writes an `audit_log` row for every `act`-bearing request and sets `X-Impersonated-By`.
  **Accepted risk (user decision 2026-10-18):** the audit row is written after the handler in its own
  session, so a failed audit write on a successful request returns 500 although the change is committed
  (a client retry may repeat it). Revisit by moving the audit write into `current_principal` (before the handler).
- **Input:** Pydantic `extra="forbid"` on every request model; `EmailStr`; IANA timezones via `zoneinfo`.
- **Uploads:** server-chosen keys (`avatars/{uuid7}`, `logos/{uuid7}`), presign constrains type/size, confirm re-checks via HEAD; SVG sanitised on confirm.
- **Privacy:** structlog redaction by key name; IP and user agent stored as salted SHA-256; erasure per US-01.13 plus `share_links.target_email` and `one_time_tokens`.
- **Supply chain:** `pip-audit` in CI.

#### 6. Scalability

- **Stateless API:** all session state in Postgres/Redis; API and worker scale horizontally.
- **argon2 cost containment:** hashing runs in a thread (`anyio.to_thread.run_sync`) behind a process-wide
  semaphore sized to CPU count, so 64 MiB × concurrency is bounded and the event loop never blocks (NFR-001, NFR-007).
- **Hot-path reads:** `current_principal` is one indexed query; no per-request cache, so revocation is immediate.
  Revisit (short Redis cache + invalidation on status change) only if `GET /me` misses 100 ms p95.
- **Concurrency guards by constraint, not locks, where possible:** `uq_coach_one_active_trainer`,
  `uq_users_email`, availability `EXCLUDE`, atomic OTT consume. `SELECT … FOR UPDATE` only for
  ShareLink redemption, refresh rotation and approval decisions.
- **Indexes:** partial indexes on open tokens/active links; `pg_trgm` for the admin directory (NFR-003).
- **Caching:** public branding via `Cache-Control: public, max-age=300` only (G-26).
- **Async work:** all email and expiry work in ARQ jobs with idempotency keys and exponential backoff (5 attempts → dead-letter log).

#### 7. Key decisions (ADR-style)

| # | Decision | Alternatives rejected | Why |
|---|---|---|---|
| A-01 | Horizontal layers (`routes/services/repositories`) | Vertical feature packages | Conv §2 enforces layering |
| A-02 | Player tenant passed per request (`?trainer_id=`), validated by `player_context` | Context-switch endpoint re-issuing tokens; `X-Trainer-Id` header | Stateless, explicit in OpenAPI, cacheable per URL, no token churn; `GET /me/contexts` lists valid values |
| A-03 | DB re-validation of every principal (status, coach tenure, tenant status, impersonation) | Trust JWT claims until expiry | Deactivation and impersonation exit must be immediate (US-01.12, US-01.07) |
| A-04 | `coach_profiles` surrogate PK, one row per tenure, partial UQ on active | `user_id` PK with in-place `trainer_id` update | Preserves history; unique index is the race guard |
| A-05 | Single `one_time_tokens` table keyed by `purpose` | One table per token type; signed stateless tokens (JWT) | Single-use and supersede need server state; one lifecycle, one repository |
| A-06 | Cross-epic behaviour behind `typing.Protocol` + fakes | Direct imports, or stubbing whole endpoints | Epic-01 ships and tests independently of Epics 02/05/07 |
| A-07 | Third-party libraries wrapped in `core/` | Direct use in services | Replaceable (zxcvbn, uuid-utils), testable with fakes |
