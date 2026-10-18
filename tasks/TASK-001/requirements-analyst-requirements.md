# Phase 0 Foundation + Epic-01 (User Management & Authentication) Requirements

## Overview

TASK-001 covers **Plan Phase 0 + Phase 1A**:

1. **Phase 0 — Foundation**: the FastAPI service skeleton that every epic assumes (error contract,
   pagination envelope, injected `Clock`, idempotency, logging, test tiers, CI).
2. **Epic-01 — Identity & Tenancy**: authentication, rotating refresh tokens, RBAC, tenant isolation,
   the profile graph (trainer / coach / player / parent→child), ShareLinks, availability, child
   purchase approvals, admin impersonation, GDPR erasure, trainer portal branding. 14 user stories.
3. **Cross-cutting stubs** (owned by Epic-07, built here per Plan §1): `AuditService` and
   `FeatureFlagService`.

Epic-01 is P0. It blocks Epics 02–08: they all get "who is calling, which tenant, what may they touch"
from the `deps.py` built here.

## Source

- `python-accelerator-main/Task/Epics/Epic-01_User_Management_Authentication_SPEC.md` (primary)
- `python-accelerator-main/Task/Epics/00_Backend_Conventions.md` (stack, layering, tenancy, auth,
  API/error/pagination contract, concurrency, testing, DoD). Cited below as **Conv §N**.
- `python-accelerator-main/Task/Epics/Epic_Areas_Plan_Python.md` (delivery order, module map, merge
  gates). Cited below as **Plan §N**.
- User decision (this session): scope = Phase 0 + Epic-01, plus the AuditService/FeatureFlagService stubs.

Current repo state: no `app/` code exists. `specs/MANIFEST.md` is still the unfilled template.

---

## Functional Requirements

### A. Phase 0 — Foundation (prerequisite, no user stories)

1. **FR-001: Project skeleton.** uv project (Python 3.12), FastAPI app, layered layout per Conv §2
   (`api/v1/<domain>/{routes,schemas}.py`, `api/deps.py`, `services/`, `repositories/`, `models/`,
   `jobs/`, `core/`, `db/`). `tests/{unit,integration,e2e,load}/`.
   - Acceptance: `uv sync && uv run pytest` passes on an empty suite. `ruff check`, `ruff format --check`,
     `mypy --strict app` are clean.
   - Priority: High
2. **FR-002: DB layer.** SQLAlchemy 2.0 async (asyncpg), typed `Mapped[...]` base with `id UUIDv7`,
   `created_at` and `updated_at` (`timestamptz`, server default `now()`). Alembic env with tested downgrade.
   Postgres extensions `citext` and `btree_gist`.
   - Acceptance: `alembic upgrade head` then `downgrade base` round-trips in CI.
   - Priority: High
3. **FR-003: Error contract.** Domain exceptions (`NotFoundError` 404, `PermissionDeniedError` 403,
   `ConflictError` 409, `PaymentRequiredError` 402, `RateLimitedError` 429, `ExternalServiceError` 502,
   validation 422). One handler emits `{"error": {"code", "message", "details"}}`.
   - Acceptance: every status above has a test checking the response shape.
   - Priority: High
4. **FR-004: Cursor pagination.** Envelope `{items, next_cursor, has_more}`, `limit` default 50 and max 200,
   opaque cursor.
   - Priority: High
5. **FR-005: `Clock` Protocol.** Injectable clock. Domain code never calls `datetime.now()`.
   - Priority: High
6. **FR-006: Idempotency.** `idempotency_keys` table with `UNIQUE (key, principal_id)`, plus a
   dependency/middleware that replays the stored response on a repeated key.
   - Acceptance: two identical requests with the same `Idempotency-Key` produce one side effect and
     identical responses. A different request hash under the same key ⇒ 409 (assumption, see Gap G-24).
   - Priority: High (needed by US-01.05)
7. **FR-007: Observability.** structlog JSON logs with a correlation id per request. A PII redaction
   processor drops email, phone, DOB, password, token and notes by key name. OpenTelemetry hook point.
   - Priority: Medium
8. **FR-008: Rate limiter.** Redis fixed-window limiter keyed by (route class, IP, principal).
   Returns `429` + `Retry-After`. Limits per Conv §4.
   - Priority: High
9. **FR-009: Infrastructure ports.** `EmailSender` Protocol + one adapter + an in-memory fake.
   `ObjectStorage` Protocol (presign PUT, HEAD, GET URL) over boto3 + a fake. ARQ worker with cron
   registry. Retry policy: exponential backoff, 5 attempts, then dead-letter log.
   - Priority: High
10. **FR-010: Local/CI environment.** docker compose with Postgres 16 and Redis 7. CI runs ruff, mypy,
    pytest (all tiers) and `pip-audit`.
    - Priority: High
11. **FR-011: Cross-cutting stubs.** `AuditService.record(actor_id, action, target, metadata, act=None)`
    persists to an `audit_log` table (minimal schema, Epic-07 extends it).
    `FeatureFlagService.is_enabled(flag, trainer_id)` returns a default from config/table.
    Both are defined as Protocols so Epic-07 can replace the implementation.
    - Priority: High

### B. Authentication & session

12. **FR-012: Login.** `POST /api/v1/auth/login`, email + password, argon2id.
    - Acceptance: success ⇒ access JWT (15 min; claims `sub`, `role`, `trainer_id`, `act`, `jti`) and an
      opaque refresh token (256-bit, stored SHA-256, 30 d). Every failure ⇒ `401 INVALID_CREDENTIALS`.
      A missing user still runs a dummy hash. Updates `last_login_at`. Response includes
      `email_verified` and `must_change_password`. Inactive and deleted users are rejected.
      Rate limit: 10/min per IP and 5/min per email.
    - Priority: High
13. **FR-013: Refresh rotation with reuse detection.** `POST /auth/refresh`.
    - Acceptance: a valid token is consumed (`consumed_at`), a successor is issued in the same family,
      and a new pair is returned. Presenting a consumed token revokes the whole family and returns
      `401 REFRESH_REUSE_DETECTED` + an audit row. An expired or revoked token ⇒ `401`.
      Uses `SELECT … FOR UPDATE`.
    - Priority: High
14. **FR-014: Logout.** `POST /auth/logout` revokes the caller's refresh family.
    - Priority: High
15. **FR-015: Password reset.** `POST /auth/password-reset/request` always returns `202` (no enumeration)
    and enqueues `send_password_reset`. `POST /auth/password-reset/confirm` takes a single-use token
    (1 h TTL), sets the new password and revokes all families. Rate limit: 5/hour per email.
    - Priority: High
16. **FR-016: Email verification.** `POST /auth/verify-email` (24 h TTL, single-use) and
    `POST /auth/verify-email/resend` (authenticated, rate limited).
    Assumption (spec, Q-01.05): verification gates purchase and ShareLink redemption
    (`403 EMAIL_NOT_VERIFIED`), not login.
    - Priority: High
17. **FR-017: Forced password change.** Admin-provisioned trainers have `must_change_password = true`.
    They set a password through the invite setup token. Until then, only the password-change/setup
    endpoints are allowed. (Endpoint is missing from §8, see Gap G-03.)
    - Priority: High
18. **FR-018: Password policy.** ≥ 12 chars and zxcvbn score ≥ 3. Applied on register, reset, setup and
    change. Violations ⇒ `422 WEAK_PASSWORD`.
    - Priority: High

### C. Authorization & tenancy

19. **FR-019: `current_principal` dependency.** Yields `(user_id, role, trainer_id, act)` (plus a child
    flag, see Gap G-06). Re-reads `users.status` on every request, so deactivation takes effect
    immediately (US-01.12).
    - Priority: High
20. **FR-020: `require_role(...)` / `require_tenant_scope(...)` dependencies + explicit `SuperAdminScope`
    marker.** Tenant comes from the token, never from the body.
    - Priority: High
21. **FR-021: Permission matrix (spec §6).** Enforced in the service layer. Each ❌ cell ⇒ `403` test.
    Each "own tenant" cell ⇒ `404` test for another tenant's id.
    - Priority: High
22. **FR-022: Tenant-scoped repositories.** Must take `trainer_id`. A bare `get(id)` does not exist for
    tenant-scoped tables.
    - Priority: High

### D. User stories

| ID | Story | Endpoint(s) | Key acceptance criteria (full list in spec §9) | Priority |
|:---|:---|:---|:---|:---:|
| FR-101 / US-01.01 | Super Admin provisions trainer | `POST /admin/users/trainers` | 201 without password. Users + profiles + trainer_profiles in one transaction. Random 32-byte password. `must_change_password`. `send_trainer_invite` with 24 h setup token. `409 EMAIL_TAKEN` / `SLUG_TAKEN` derived from the unique constraint. 403 for non-admins. Audit `trainer.created`. | High |
| FR-102 / US-01.02 | Player registers via ShareLink | `POST /auth/register?share_code=` | One `404 SHARE_LINK_INVALID` for unknown, inactive, expired and exhausted links. New email ⇒ 201 + tokens. Authenticated existing user ⇒ 200, link only. Unauthenticated existing email ⇒ `409 ACCOUNT_EXISTS` (`login_then_redeem`). Parent `player_profile_ids[]` (empty ⇒ 422, not owned ⇒ 404). `409 WRONG_LINK_KIND`. Atomic `use_count`. `share_link_events(registered)`. Welcome + verification jobs. **20-way concurrency ⇒ 1 success, 19 × 404.** | High |
| FR-103 / US-01.03 | Parent creates dependent | `POST /me/players` | Required display_name, birth_date, gender. Age `1 <= age < 18` else `422 INVALID_AGE` (resolved 2026-10-18 per §10; US-01.03 says "1–18"). Optional `trainer_ids[]` must already be linked (else 404). `409 DUPLICATE_PLAYER` unless `?allow_duplicate=true`. Parent's own player profile is created implicitly at registration. | High |
| FR-104 / US-01.04 | Parent manages child↔trainer links | `POST/DELETE /me/players/{id}/trainers[/{trainer_id}]` | Add 201, or 200 if already linked. Remove is a soft `inactive`. Future paid RSVP ⇒ `409 ACTIVE_COMMITMENTS`. Not owner ⇒ 404. Detail shows `linked_at` and `source`. | High |
| FR-105 / US-01.05 | Child purchase approval | `GET /me/approvals`, `POST /me/approvals/{id}/approve\|deny` | Child purchase ⇒ `pending` + 202, never a charge. USD always needs approval. Tokens need approval if the profile flag is set. Approve calls the owning service in the same transaction. Deny + note + job. `409 APPROVAL_NOT_PENDING`. Another parent ⇒ 404. Expiry job. Idempotency-Key. **10-way parallel approve ⇒ 1 effect.** | High |
| FR-106 / US-01.06 | Child login with constraints | parent sets child login (endpoint missing, G-04) | Separate `users` row via `login_user_id`. Child token `trainer_id` limited to that profile's links. Forbidden: redeem ShareLink (`403 CHILD_CANNOT_REDEEM` + parent job), create dependents, payment methods, complete purchase. Each forbidden endpoint has a 403 test. | High |
| FR-107 / US-01.07 | Super Admin impersonates | `POST /admin/users/{id}/impersonate`, `POST /auth/impersonation/exit` | `sub=target`, `act=admin`, TTL ≤ 60 min. Target super_admin ⇒ `403 CANNOT_IMPERSONATE_ADMIN`. Session row. Exit revokes `jti`. Expired ⇒ `401 IMPERSONATION_EXPIRED`. Audit on every `act` request. `X-Impersonated-By` header. `/admin/*` ⇒ 403 while impersonating. | High |
| FR-108 / US-01.08 | Trainer invites coach | `POST /trainer/share-links` (`coach_unique`) | 201 `{code, kind, expires_at=now+7d, max_uses:1}`. Email mismatch ⇒ `403 LINK_EMAIL_MISMATCH`. Already an active coach ⇒ `409 COACH_ALREADY_ASSIGNED` (names the trainer). coach_profiles + link exhaustion in one transaction. `trainer_id` taken from the token. | High |
| FR-109 / US-01.09 | Player sets availability | `GET/PUT /me/availability`, `PUT /me/players/{id}/availability` | Full weekly replace (delete + insert in one transaction). `422 OVERLAPPING_WINDOWS` (+ GiST). start ≥ end ⇒ 422. Wall-clock times + profile tz. Parent manages a dependent's set, another owner ⇒ 404. Readable only by linked trainers. | High |
| FR-110 / US-01.10 | Coach sets My Times | `GET/PUT /me/availability` (coach) | Readable by own trainer and super_admin, everyone else ⇒ 404. `AvailabilityService.check_conflict(subject, start, end)` is a pure function, unit-tested at 15:59 / 16:00 / 18:00 / 18:01 against a 16:00–18:00 window. | High |
| FR-111 / US-01.11 | User edits own profile | `GET/PATCH /me`, `PUT /me/avatar`, `POST /me/avatar/confirm`, `POST /me/email-change` | Only names, phone, school, timezone and avatar are editable. Email change requires re-verification and the old email stays active until then. Avatar: presign then confirm (≤ 5 MB, jpeg/png/webp). `extra="forbid"` ⇒ 422. | Medium |
| FR-112 / US-01.12 | Admin deactivates user | `POST /admin/users/{id}/deactivate\|reactivate` | `inactive`. Revokes all families. Access token rejected on the next request. History untouched. Deactivated trainer ⇒ tenant hidden from players. Reactivating a deleted user ⇒ `409 USER_ERASED`. Audit both ways. | High |
| FR-113 / US-01.13 | Admin GDPR erasure | `DELETE /admin/users/{id}` | Anonymise in place (email `erased+<uuid>@invalid`, random hash, "Deleted User" / "Deleted Player", PII nulled). `deleted` + `deleted_at`. Revoke families. No FK cascades. `user_erasures` row with hashed email. Dependents erased in the same transaction. Second call ⇒ `409 ALREADY_ERASED`. **Test: no plaintext original email anywhere.** | High |
| FR-114 / US-01.14 | Trainer portal branding | `GET/PATCH /trainer/branding`, `GET /public/trainers/{slug}/branding` | Hex colour regex ⇒ 422. Logo presign flow (≤ 2 MB, png/svg+xml/webp). SVG sanitised on write. Public endpoint: unauthenticated, 5 min cache, only logo URL + colours + business name. `trainer_id` taken from the token. | Medium |

### E. In-scope items without a dedicated user story

23. **FR-201: Admin user directory.** `GET /admin/users`, cursor-paginated. Filters: role, status,
    trainer, query. `GET/PATCH /admin/users/{id}`. p95 < 500 ms at 10 k users.
24. **FR-202: ShareLink management.** `GET/POST /trainer/share-links`, `DELETE /trainer/share-links/{id}`
    (deactivate). Static player link: unlimited, no expiry. Unique coach link: single-use, 7 d.
25. **FR-203: Public ShareLink preview.** `GET /public/share-links/{code}` returns trainer name, kind and
    validity. Records `share_link_events(opened)` with salted SHA-256 IP and user-agent hashes.
26. **FR-204: Trainer reads player availability.** `GET /trainer/players/{id}/availability`,
    tenant-scoped, 404 cross-tenant.
27. **FR-205: Player trainer contexts.** `GET /me/contexts` lists active trainer links. Every
    player-owned read is per `trainer_id` and there is no cross-trainer aggregation.
28. **FR-206: Dependent CRUD.** `GET /me/players`, `GET/PATCH/DELETE /me/players/{id}`. PATCH toggles
    `token_spend_requires_approval`.
29. **FR-207: Camp-submission conversion registration.** `POST /auth/register?camp_submission_id=`,
    pre-filled from Epic-08. Built against a `CampSubmissionReader` Protocol and stubbed until
    Epic-08 exists.
30. **FR-208: Coach public profile.** Fields: bio, credentials, certifications (JSONB), `is_public`.
    Editing endpoint is undefined (G-05).
31. **FR-209: Trainer manages own organisation's users.** Endpoints are undefined (G-05).

---

## Non-Functional Requirements

1. **NFR-001: Login latency.** p95 `POST /auth/login` < 300 ms at 50 RPS (k6).
2. **NFR-002: Profile read latency.** p95 `GET /me` < 100 ms (§13). §2 says < 200 ms for "profile GETs",
   see G-20.
3. **NFR-003: Admin directory.** p95 `GET /admin/users?limit=50` < 500 ms over a 10 000-user seed.
   `EXPLAIN ANALYZE` must show an index scan.
4. **NFR-004: Registration.** p95 `POST /auth/register` < 400 ms at 100 concurrent requests. 200 concurrent
   registrations on one static link ⇒ 0 lost writes.
5. **NFR-005: Isolation.** 0 cross-tenant reads in the isolation suite (hard gate, Plan §6 merge gate).
6. **NFR-006: Impersonation audit.** 100 % of `act`-bearing requests produce an audit row.
7. **NFR-007: Argon2id cost.** `time_cost=3, memory_cost=64 MiB, parallelism=4`. Verification takes
   ≥ 100 ms on target hardware (benchmark test).
8. **NFR-008: Code quality.** Coverage ≥ 85 % on `app/services/` and `app/api/`. mypy strict, ruff clean.
9. **NFR-009: Security.** OWASP baseline (Conv §9): Pydantic at every boundary (`extra="forbid"`), no
   f-string SQL, PII never logged, secrets via pydantic-settings, uploads restricted by allowlist,
   size cap and random keys, `pip-audit` in CI.
10. **NFR-010: Documentation.** `/api/v1/openapi.json` documents every endpoint with examples and error
    codes.
11. **NFR-011: Load scripts.** Every §13 target is asserted by a script in `tests/load/`.

---

## Business Rules

**Authentication / tokens**
1. BR-001: Login fails with the same `401 INVALID_CREDENTIALS` in every case, and in constant time
   (dummy verify when the user is missing).
2. BR-002: Password ≥ 12 chars and zxcvbn ≥ 3.
3. BR-003: Refresh tokens rotate on every use. Reusing a consumed token revokes the family and writes
   an audit row.
4. BR-004: Access TTL 15 min, refresh TTL 30 d (Q-01.07 assumption), password reset 1 h, email
   verification and trainer setup 24 h. All one-time tokens are single-use.
5. BR-005: An unverified email may log in. Purchase and ShareLink redemption ⇒ `403 EMAIL_NOT_VERIFIED`.

**Tenancy**
6. BR-010: Only super_admin creates trainers. There is no trainer self-registration.
7. BR-011: `trainer_player_links` alone defines player↔tenant membership. Every other epic joins through it.
8. BR-012: A player may link to many trainers. Reads are always per `trainer_id` and never aggregated.
9. BR-013: A coach is active under exactly one trainer (partial unique index). Moving deactivates the
   old row in the same transaction.
10. BR-014: Cross-tenant access ⇒ 404, never 403.
11. BR-015: The tenant comes from the principal. `SuperAdminScope` bypass is explicit per endpoint.

**ShareLinks**
12. BR-020: `player_static` ⇒ no expiry and no max uses. `coach_unique` ⇒ expires in 7 d and
    `max_uses = 1` (DB CHECKs).
13. BR-021: Codes are 22-char `secrets.token_urlsafe`.
14. BR-022: An invalid code of any kind ⇒ the same `404 SHARE_LINK_INVALID` (no oracle).
15. BR-023: `use_count` is incremented by an atomic conditional UPDATE or under `FOR UPDATE`, never
    read-then-write.
16. BR-024: Children cannot redeem. The parent is notified instead.
17. BR-025: IP and user agent are stored only as salted SHA-256.

**Parent / child**
18. BR-030: Every dependent under 18 is owned by a parent account. There is no independent minor account.
19. BR-031: Contact details of record live on the parent's `profiles` row.
20. BR-032: A child USD purchase always needs approval. Token spend needs approval when the profile flag
    is set (default true).
21. BR-033: Approvals expire at 48 h and cannot be resurrected. Approve is idempotent.
22. BR-034: Removing a child↔trainer link is a soft delete and is blocked by future paid RSVPs.

**Impersonation**
23. BR-040: Never targets a super_admin. Max 60 min. Every request audited. `/admin/*` blocked while
    impersonating.

**Erasure / lifecycle**
24. BR-050: Anonymise in place. Never `DELETE FROM users`. Every FK is `ON DELETE RESTRICT`.
25. BR-051: An erased user cannot be reactivated. Erasure is irreversible.
26. BR-052: Erasing a parent erases their dependents in the same transaction.
27. BR-053: Deactivation is soft and keeps history. Status is re-checked on every request.

**Availability**
28. BR-060: Weekly windows are a full-replace set with no overlaps on the same weekday and
    `start < end`. They are stored as local wall-clock time plus the profile's IANA timezone. Conflict
    checks use the event's timezone.

**Uploads**
29. BR-070: Avatar ≤ 5 MB, jpeg/png/webp. Logo ≤ 2 MB, png/svg+xml/webp. SVG is sanitised on write
    (script and foreignObject stripped).

---

## Task Breakdown

### Phase 0 — Foundation tasks
- [ ] uv project, pyproject (ruff, mypy strict, pytest-asyncio), compose (pg16, redis7), CI workflow
- [ ] `core/config.py` (pydantic-settings), `core/errors.py` + exception handler, `core/pagination.py`,
      `core/clock.py`, `core/logging.py` (structlog + redaction), `core/idempotency.py`, `core/ratelimit.py`
- [ ] `db/session.py`, `db/base.py` (UUIDv7 PK mixin, timestamps), Alembic env + baseline migration
      (citext, btree_gist, `timerange` range type, `idempotency_keys`, `audit_log`, `feature_flags`)
- [ ] `core/email.py` (`EmailSender` Protocol + adapter + fake), `core/storage.py` (S3 Protocol + fake)
- [ ] `jobs/worker.py` (ARQ settings, retry/backoff, dead-letter, cron registry)
- [ ] `services/audit_service.py`, `services/feature_flag_service.py` (Protocols + stub implementations)
- [ ] Test fixtures: app factory, AsyncClient, Postgres (testcontainers), Redis, FakeClock, FakeEmail,
      principal/token factory, tenant factory

### Models (`app/models/identity.py`)
| Model | Properties | Relations |
|-------|------------|-----------|
| User | email (citext UQ), password_hash, role, status (default pending), is_email_verified, must_change_password, last_login_at, deleted_at | 1:1 Profile. 0..1 TrainerProfile / CoachProfile. Owns many PlayerProfiles |
| Profile | first_name, last_name, phone, avatar_key, school, timezone (IANA, default UTC) | PK/FK → users |
| TrainerProfile | business_name, slug (UQ), address, website, description, logo_key, brand_primary/secondary_color (CHAR(7) CHECK hex) | PK/FK → users. Tenant root |
| CoachProfile | trainer_id, bio, credentials, certifications (JSONB), is_public, joined_at, **is_active** (G-01) | → users, → trainer_profiles. Partial UQ (user_id) WHERE is_active |
| PlayerProfile | owner_user_id, login_user_id?, display_name, birth_date, gender, skill_level?, school, jersey_number, is_dependent, emergency_contact_name/phone, token_spend_requires_approval (default true) | → users (owner, login). Many TrainerPlayerLinks |
| TrainerPlayerLink | trainer_id, player_profile_id, source enum, share_link_id?, linked_at, status | UQ (trainer_id, player_profile_id). Index (trainer_id, status) |
| ShareLink | code (UQ), trainer_id, created_by_user_id, kind, target_email?, expires_at?, max_uses?, use_count, is_active | CHECKs per kind |
| ShareLinkEvent | share_link_id, kind (opened/registered), occurred_at, user_agent_hash, ip_hash | → share_links |
| AvailabilityWindow | subject_type, subject_id, weekday 0–6, start_time, end_time, is_available | CHECK start<end. GiST EXCLUDE on overlap |
| PurchaseApproval | player_profile_id, parent_user_id, trainer_id, subject_type, subject_id, amount_cents?, token_amount?, status, requested_at, responded_at?, expires_at, parent_note | Index (status, expires_at) |
| ImpersonationSession | admin_user_id, target_user_id, started_at, ended_at?, expires_at, reason?, jti | → users ×2 |
| UserErasure | original_user_id (no FK), original_email_hash, erased_by_user_id, reason, erased_at, legal_retention_until | ledger |
| RefreshToken | user_id, family_id, token_hash (UQ), expires_at, consumed_at?, revoked_at?, user_agent_hash, ip_hash | partial index (family_id) WHERE revoked_at IS NULL |
| **OneTimeToken** (G-02, not in spec) | user_id, purpose (email_verify / password_reset / trainer_setup / email_change), token_hash (UQ), expires_at, used_at?, payload (e.g. new_email) | → users |
| IdempotencyKey (Phase 0) | key, principal_id, endpoint, request_hash, response_status, response_body | UQ (key, principal_id) |
| AuditLog (stub) | actor_id, act_id?, action, target_type, target_id, metadata, method?, path? | Epic-07 extends |

### Repositories
| Repository | Notes |
|---|---|
| UserRepository | get_by_email (citext), directory query (filters + cursor), status update, anonymise |
| ProfileRepository / TrainerProfileRepository / CoachProfileRepository | slug lookup, active coach lookup |
| PlayerProfileRepository | by owner, by login user, duplicate check |
| TrainerPlayerLinkRepository | **trainer_id required**. Upsert/idempotent add, soft remove |
| ShareLinkRepository | lock by code (`FOR UPDATE`), atomic conditional increment |
| ShareLinkEventRepository | append |
| AvailabilityRepository | replace set (delete + insert), get by subject |
| PurchaseApprovalRepository | **trainer/parent scoped**. Lock by id, expire sweep |
| ImpersonationSessionRepository | open/close/expire, by jti |
| RefreshTokenRepository | lock by hash, revoke family, purge |
| OneTimeTokenRepository | issue, consume (single-use), purge |
| UserErasureRepository | append |
| IdempotencyRepository, AuditRepository | Phase 0 |

### Services
| Service | Purpose | Methods |
|---------|---------|---------|
| PasswordHasher (core/security) | argon2id + dummy verify + policy | hash, verify, validate_policy |
| TokenIssuer (core/security) | JWT encode/decode, refresh generation | issue_access, issue_pair, decode |
| AuthService | login, refresh, logout, reset, verify, setup/change password, register (ShareLink/camp) | login, refresh, logout, request_reset, confirm_reset, verify_email, resend_verification, change_password, register |
| UserService | profile, email change, avatar, admin directory, provision trainer, deactivate/reactivate, erase | get_me, update_me, start_email_change, presign_avatar, confirm_avatar, list_users, provision_trainer, update_user, deactivate, reactivate, erase |
| PlayerService (or part of UserService) | dependents, child login, child↔trainer links, contexts | create_dependent, update/delete_dependent, set_child_login, link_trainer, unlink_trainer, list_contexts |
| ShareLinkService | issue, deactivate, preview, redeem (player + coach) | issue, list, deactivate, preview, redeem |
| AvailabilityService | replace/get windows, tenant-scoped read, **pure** `check_conflict` | replace, get_own, get_for_trainer, check_conflict |
| ApprovalService | create pending, approve (delegates via `PurchaseExecutor` Protocol → Epic-02/05), deny, expire | request, approve, deny, list_pending, expire_due |
| ImpersonationService | start, exit, validate, expire | start, exit, assert_active, expire_due |
| BrandingService | get/patch colours, logo presign/confirm + SVG sanitise, public read (cached) | get, update, presign_logo, confirm_logo, get_public |
| AuditService / FeatureFlagService | Stubs (Phase 0) | record / is_enabled |

### Endpoints (base `/api/v1`)
| Resource | Endpoints | Purpose |
|----------|-----------|---------|
| auth | `POST /auth/register`, `/login`, `/refresh`, `/logout`, `/password-reset/request`, `/password-reset/confirm`, `/verify-email`, `/verify-email/resend`, `/impersonation/exit`, **`/password/setup` (G-03; `/password/change` dropped 2026-10-18, not required)** | Session lifecycle |
| me | `GET/PATCH /me`, `PUT /me/avatar`, `POST /me/avatar/confirm`, `POST /me/email-change` (+ confirm, G-05), `GET/PUT /me/availability`, `GET /me/contexts` | Own profile |
| me/players | `GET/POST /me/players`, `GET/PATCH/DELETE /me/players/{id}`, `PUT /me/players/{id}/availability`, `POST /me/players/{id}/trainers`, `DELETE /me/players/{id}/trainers/{trainer_id}`, **child-login endpoint (G-04)** | Dependents |
| me/approvals | `GET /me/approvals`, `POST /me/approvals/{id}/approve`, `POST /me/approvals/{id}/deny` | Parent approvals |
| trainer | `GET/POST /trainer/share-links`, `DELETE /trainer/share-links/{id}`, `GET /trainer/players/{id}/availability`, `GET/PATCH /trainer/branding` (+ logo presign/confirm), **coach availability read (G-05)** | Tenant operations |
| public | `GET /public/share-links/{code}`, `GET /public/trainers/{slug}/branding` | Unauthenticated |
| admin | `GET /admin/users`, `POST /admin/users/trainers`, `GET/PATCH /admin/users/{id}`, `POST /admin/users/{id}/deactivate`, `/reactivate`, `DELETE /admin/users/{id}`, `POST /admin/users/{id}/impersonate` | Super Admin |

### Background jobs (`app/jobs/identity_jobs.py`)
| Job | Trigger | Idempotency key |
|---|---|---|
| send_trainer_invite | US-01.01 | setup_token_id |
| send_welcome_email | US-01.02 | user_id + 'welcome' |
| send_verification_email | register / resend | token_id |
| send_password_reset | reset request | token_id |
| notify_parent_approval_request | US-01.05 | approval_id + 'requested' |
| notify_child_decision | US-01.05 | approval_id + status |
| notify_parent_share_link_blocked | US-01.06 | player_profile_id + share_link_id |
| expire_purchase_approvals | cron 5 min | per-row status guard |
| expire_impersonation_sessions | cron 5 min | per-row guard |
| purge_expired_tokens | cron nightly | — |

### Testing Tasks
- [ ] **Unit**: PasswordHasher (policy, dummy verify), TokenIssuer, `check_conflict` minute boundaries,
      ShareLink validation rules, age computation (boundaries 1/18 with FakeClock), approval state
      machine, SVG sanitiser (hostile payloads), erasure anonymiser, permission matrix rules.
- [ ] **Integration** (real Postgres): every repository, including tenant-required signatures. DB
      constraints (share_link CHECKs, coach partial UQ, GiST exclusion, hex CHECK, `ON DELETE
      RESTRICT`). Trainer provisioning rollback. Refresh rotation + reuse family revoke. Erasure:
      no-plaintext scan across all tables. Each job (at least 1 test each). Alembic downgrade.
- [ ] **E2E** (AsyncClient + PG + Redis): every US acceptance criterion as a named test.
      Permission matrix: one 403 test per ❌ cell, one 404 test per "own tenant" cell. Cross-tenant 404
      on every tenant-scoped route. Child-forbidden 403s. Impersonation audit coverage + header +
      admin block. Rate limits (`429` + `Retry-After`). Idempotent approve.
- [ ] **Concurrency**: 20-way coach_unique redemption ⇒ 1 success / 19 × 404. 200-way static link
      registration ⇒ 0 lost writes. 10-way parallel approve ⇒ 1 effect. Parallel refresh with the same
      token ⇒ 1 success + family revoked.
- [ ] **Time boundaries** (FakeClock): approval 48 h, impersonation 60 min, reset 1 h, verify/setup
      24 h, coach link 7 d, refresh 30 d, access 15 min.
- [ ] **Load** (`tests/load/`, k6 or locust): every §13 target against a 10 000-user seed. Argon2
      benchmark ≥ 100 ms.

### Merge gates owned by this task (Plan §6)
- Cross-tenant `404` on every tenant-scoped route
- 20-way ShareLink redemption ⇒ exactly 1 success

---

## Validation Checklist

- [x] All functional requirements mapped to tasks (FR-001…FR-209 ↔ models/services/endpoints/tests)
- [x] Happy path covered (per-US e2e tests)
- [x] Error cases identified (every error code in spec §9 + Conv §5 has a negative test)
- [x] Edge cases considered (time boundaries, concurrency, idempotency, erasure of dependents, reuse detection)
- [x] Security requirements addressed (RBAC matrix, tenancy 404s, constant-time login, no enumeration, rate limits, PII redaction, SVG sanitising, hashed tokens/IPs)
- [x] Performance requirements noted (NFR-001…004, load scripts)
- [x] Testing strategy defined (unit / integration / e2e / concurrency / time / load)
- [ ] Open spec gaps resolved (see Gap Analysis)

---

## Gap Analysis

### Schema defects / inconsistencies (must fix before migrations)
- [ ] **G-01 `coach_profiles` cannot express "move trainers".** `user_id` is the PK, yet the partial
      index `… (user_id) WHERE is_active` references an `is_active` column that is not defined, and
      "deactivate the old row" needs more than one row per user. Proposal: surrogate `id` PK + `is_active`
      column + partial UQ.
- [ ] **G-02 No storage for one-time tokens.** Trainer setup (24 h), password reset (1 h), email
      verification (24 h) and email change all need a hashed single-use token table.
      `purge_expired_tokens` already refers to "verification rows". Proposal: `one_time_tokens`.
- [ ] **G-07 `CHECK (age_years(birth_date) BETWEEN 1 AND 18)` is not a valid Postgres CHECK.** It depends
      on the current date (non-immutable), and existing rows would "become invalid" as children age.
      Proposal: validate in the service with the injected `Clock`. Also: is 18 itself allowed? §7 says
      `BETWEEN 1 AND 18`, BR says "age < 18".
- [ ] **G-08 `CHECK (NOT is_dependent OR owner_user_id IS NOT NULL)` is redundant** if `owner_user_id`
      is always required (an adult's profile is owned by the adult). Confirm `owner_user_id NOT NULL`.
- [ ] **G-09 GiST exclusion needs setup.** Postgres has no built-in `timerange` type. It needs
      `CREATE TYPE timerange AS RANGE (subtype = time)` + `btree_gist`, and an `is_available = false`
      window probably should not take part in the exclusion. Confirm the semantics of `is_available`.
- [ ] **G-10 Tenancy rule vs the schema.** Conv §3 says every tenant-scoped table has `trainer_id NOT NULL`.
      `player_profiles` and `availability_windows` don't (tenancy goes through `trainer_player_links`).
      Confirm this is an intended exception and record it in architecture.
- [ ] **G-11 UUIDv7 on Python 3.12.** `uuid.uuid7` arrives only in Python 3.14 and Postgres 16 has no
      native generator. A dependency (e.g. `uuid6`/`uuid-utils`) is needed but not listed.
      The local interpreter is Python 3.10.12, and the target is 3.12.

### Missing endpoints (behaviour specified, no route in §8)
- [ ] **G-03** Trainer first-login / forced password change: endpoint to consume the setup token and
      set a password (e.g. `POST /auth/password/setup`), and an authenticated `POST /auth/password/change`.
      Which endpoints remain allowed while `must_change_password = true`?
- [ ] **G-04** Parent creates or removes a child login (US-01.06). Route, payload (child email
      required? username?) and verification flow for a child's email are undefined.
- [ ] **G-05** Not in §8: `POST /me/avatar/confirm`, `POST /me/email-change` (+ confirm), `PUT
      /me/players/{id}/availability`, logo presign/confirm, trainer reads coach availability
      (US-01.10), coach public-profile edit (FR-208), "trainer manages own organisation's users"
      (FR-209, no endpoints at all).

### Behaviour ambiguities
- [ ] **G-06 Principal model for players with several trainers and for children.** The token carries one
      `trainer_id`, but a player has many contexts (`/me/contexts`). How is the active context chosen:
      a context-switch endpoint that re-issues the token, an `X-Trainer-Id` header checked against
      links, or a path parameter? There is also no "child" marker in `(user_id, role, trainer_id, act)`,
      and the role enum has only `player`. Proposal: derive `is_child` from
      `player_profiles.login_user_id` and add it to the principal.
- [ ] **G-12 Coach-link redemption contradicts itself.** US-01.02 says "`coach_unique` redeemed by a
      non-coach ⇒ 409 WRONG_LINK_KIND", but a new coach has no account before redeeming. Does
      registering with a coach link create `users(role=coach)`? Can an existing player become a coach
      (one role per user ⇒ no)?
- [ ] **G-13 `users.status` lifecycle is undefined.** When does `pending` become `active` (email
      verification, first login, password setup)? May `pending` users log in?
- [ ] **G-14 Impersonation refresh.** "Returns a token pair": may the impersonation refresh token
      outlive the 60-min session? Proposal: refresh is bounded by session `expires_at`, or no refresh
      at all.
- [ ] **G-15 Hiding a deactivated trainer's tenant (US-01.12).** The mechanism is unspecified (filter
      on `users.status` in context listing and public endpoints?). What happens to the trainer's
      coaches and active ShareLinks?
- [ ] **G-16 The password policy mixes two checks.** zxcvbn is a strength estimator, not a breached-
      password list. Is a HIBP-style k-anonymity check also required? `zxcvbn` is missing from the
      package list.
- [ ] **G-17 Rate limit for `verify-email/resend`** is required but no value is defined (Conv §4
      covers login and password reset only). The same applies to `POST /auth/register` and
      `GET /public/share-links/{code}` (code enumeration).
- [ ] **G-18 Erasure scope.** `share_links.target_email`, pending email-change tokens, audit-log
      metadata and job payloads may hold the original email. The "no plaintext anywhere" test must
      cover them. The value of `legal_retention_until` is unspecified.
- [ ] **G-19 SVG sanitisation library.** Conv §9 names `bleach` (HTML-only, deprecated). An SVG-safe
      approach (defusedxml + allowlist) needs choosing.
- [ ] **G-20 Latency target conflict.** §2 says profile GETs p95 < 200 ms, §13 says `GET /me` < 100 ms.
- [ ] **G-21 Wrong cross-reference.** US-01.09 cites "US-01.14 tenancy", but US-01.14 is branding.
- [ ] **G-22 Conv §5 forbids `PUT` on resources**, yet the spec uses `PUT /me/avatar` and
      `PUT /me/availability`. Accept as documented exceptions (full replace is genuine PUT semantics)
      or rename.
- [ ] **G-23 `ACTIVE_COMMITMENTS` (US-01.04) and approval execution (US-01.05) need Epic-02/05 data.**
      They are built against Protocols (`CommitmentChecker`, `PurchaseExecutor`) with fakes until those
      epics land. Confirm this is acceptable for Epic-01 DoD.
- [ ] **G-24 Idempotency edge cases.** Behaviour for the same key + different body, and key TTL, are
      unspecified. Assumption: 409 `IDEMPOTENCY_KEY_REUSED`, 24 h retention.
- [ ] **G-25 Admin directory "query" filter.** Searches email/name? Meeting 500 ms at 10 k rows with
      `ILIKE` likely needs a `pg_trgm` index. This is not in the schema.
- [ ] **G-26 Public branding "cached 5 min".** HTTP `Cache-Control` only, or a Redis cache with
      invalidation on PATCH?

### Open questions carried from spec §14 (assumptions in force)
- [ ] Q-01.01 skill_level enum (fixed vs trainer-defined). **Proposal:** nullable `text` until resolved.
- [ ] Q-01.02 age grouping (Epic-02 concern, no Epic-01 schema impact beyond `birth_date`).
- [ ] Q-01.04 launch email set. **Assumption:** all 7 email jobs in §12.
- [ ] Q-01.05 verification gate. **Assumption (spec):** gates purchase + ShareLink redemption, not login.
- [ ] Q-01.06 coach notification on override (Epic-02).
- [ ] Q-01.07 refresh TTL. **Assumption (spec + Conv §4):** 30 days.
- [ ] Q-01.03 is missing from the spec's numbering. Check whether a question was dropped.
