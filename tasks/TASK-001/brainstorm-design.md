# Phase 0 Foundation + Epic-01 Identity & Tenancy Design

> Requirements: [requirements-analyst-requirements.md](requirements-analyst-requirements.md) (FR/NFR/BR ids, gap ids G-xx).
> Sources (only these): Epic-01 SPEC, `00_Backend_Conventions.md` (Conv), `Epic_Areas_Plan_Python.md` (Plan).
> This document records **decisions and deltas**. Anything not restated here follows the spec as written.

## Problem Statement

The service has no code yet. Every later epic depends on Epic-01's answer to three questions: who the
caller is, which tenant they act in, and what they may touch (`deps.py`). Epic-01 cannot be built
without the Phase 0 foundation (error contract, pagination, Clock, idempotency, rate limiter, jobs).
The spec also leaves 26 gaps that block a clean schema and API. This design resolves them.

## Proposed Solution

One FastAPI service, horizontally layered as Conv §2 requires, following the Plan §2 module map.
Delivery order:

1. Phase 0 foundation.
2. Identity schema, security core and the `deps.py` dependencies.
3. The Epic-01 services and routes.
4. Background jobs.

Cross-epic needs named inside Epic-01 are `typing.Protocol` seams with fakes (Plan §4).

**Approach considered and rejected**: vertical feature packages (`app/identity/{routes,service,repo}.py`).
They read well, but they violate the enforced layering in Conv §2.

## Architecture

```
app/
  core/        config · errors · pagination · clock · logging(PII redaction) · idempotency
               ratelimit (limits) · security (argon2id, JWT, password policy) · ids (uuid-utils)
               email (EmailSender Protocol) · storage (ObjectStorage Protocol) · svg (defusedxml allowlist)
  db/          session · base (UUIDv7 PK + timestamps mixin) · alembic/
  models/      identity.py · admin.py (audit_log, feature_flags stubs) · common.py (idempotency_keys)
  repositories/ user · profile · player · trainer_player_link · share_link · availability · approval
               impersonation · refresh_token · one_time_token · erasure · audit
  services/    auth · user · player · sharelink · availability · approval · impersonation · branding
               audit (stub) · feature_flag (stub)
  api/deps.py  get_session · current_principal · require_role · require_tenant_scope · player_context
               SuperAdminScope · idempotency · rate_limit(route_class)
  api/v1/      auth/ me/ trainer/ public/ admin/
  jobs/        identity_jobs.py · worker.py (ARQ settings, retry/backoff, dead-letter, cron registry)
```

`PlayerService` is split out of the Plan's `user_service`. It owns dependents, child login,
child↔trainer links and contexts.

### `deps.py`: the contract every epic consumes

- **`Principal(user_id, role, trainer_id: UUID | None, act: UUID | None, child: bool)`**
  - `current_principal` decodes the JWT and **re-reads `users.status`** on every request (US-01.12).
  - It rejects an impersonation token whose session has ended or expired (`401 IMPERSONATION_EXPIRED`).
  - It rejects a principal whose trainer tenant is deactivated (404, per G-15).
- **Tenant source by role (decision G-06)**:
  - trainer ⇒ `trainer_id` = self
  - coach ⇒ `trainer_id` = the trainer of the active `coach_profiles` row
  - player ⇒ `trainer_id = None` in the token. The context arrives per request as `?trainer_id=`.
- **`player_context(trainer_id: Query)`** checks for an active `trainer_player_links` row on a profile
  the caller owns. For a child, it checks only the child's own profile. A miss ⇒ **404**.
  `GET /me/contexts` lists the valid values.
- **`require_role(*roles)`** ⇒ 403. **`SuperAdminScope`** is an explicit, per-endpoint bypass marker.
  Every `/admin/*` router also rejects principals with `act` set (US-01.07).

### Cross-epic seams (Protocol + fake)

| Protocol | Source in provided files | Used by |
|---|---|---|
| `CommitmentChecker.future_paid_rsvps(player_profile_id, trainer_id) -> list[UUID]` | Epic-01 US-01.04 `ACTIVE_COMMITMENTS` | PlayerService.unlink_trainer |
| `PurchaseExecutor.execute(approval) -> None` (same transaction) | Epic-01 US-01.05, §11 approval flow | ApprovalService.approve |
| `CampSubmissionReader.get_prefill(submission_id)` | Epic-01 §3, §8 `?camp_submission_id=` | AuthService.register |
| `AuditService.record(...)`, `FeatureFlagService.is_enabled(...)` | Plan §1 (built in the Epic-01 slice) | all services |

### Middleware

- A correlation id per request.
- The `X-Impersonated-By` response header.
- The impersonation audit hook: every `act`-bearing request writes an `audit_log` row with method,
  path and target (NFR-006).

### Library decisions

| Need | Choice | Rationale |
|---|---|---|
| UUIDv7 | **uuid-utils** | RFC 9562 final, actively maintained. Python 3.12 stdlib has no uuid7 |
| Password strength | **zxcvbn** (score ≥ 3) + length ≥ 12 | **User decision: zxcvbn only.** The "breached-password list" clause in spec §10 is dropped and the spec needs amending |
| SVG sanitising | **defusedxml + custom allowlist** | Custom by justification: no maintained Python SVG sanitiser is verified to strip `foreignObject`, and `bleach` is HTML-only and deprecated. defusedxml covers XXE and bombs. Our code is only the allowlist, covered by hostile-payload tests |
| Rate limiting | **limits** (async Redis, FixedWindow) wrapped in our own dependency | Mature library. The wrapper raises `RateLimitedError` (429 + `Retry-After`) |
| Fixed by the spec | fastapi, pydantic[email], sqlalchemy[asyncio], asyncpg, alembic, argon2-cffi, pyjwt, redis, arq, boto3, python-multipart, structlog | Epic-01 §5, Conv §1 |

## Data Model

Base: Epic-01 §7 as written. **Deltas only:**

| Table | Change | Gap |
|---|---|---|
| `coach_profiles` | Surrogate `id` PK. `user_id` becomes a plain FK. Adds `is_active bool NOT NULL default true`. Partial UQ `uq_coach_one_active_trainer (user_id) WHERE is_active`. A trainer move deactivates the old row and inserts the new one in one transaction | G-01 |
| `player_profiles` | `owner_user_id NOT NULL`. The `is_dependent ⇒ owner` CHECK is dropped as redundant. The age CHECK is dropped and replaced by a service check using `Clock` (dependent age `1 <= age < 18` per spec §10; user decision 2026-10-18) | G-07, G-08 |
| `availability_windows` | Migration: `CREATE EXTENSION btree_gist`, `CREATE TYPE timerange AS RANGE (subtype = time)`. Exclusion on `timerange(start_time, end_time, '[)')`, so touching windows don't overlap. Applies to all rows | G-09 |
| `one_time_tokens` **(new)** | `id`, `user_id FK RESTRICT`, `purpose enum(trainer_setup, password_reset, email_verify, email_change)`, `token_hash text UNIQUE` (SHA-256), `payload jsonb NULL` (`{new_email}`), `expires_at`, `used_at NULL`. Partial index `(user_id, purpose) WHERE used_at IS NULL`. Consumed atomically with `UPDATE … SET used_at=now() WHERE token_hash=:h AND purpose=:p AND used_at IS NULL AND expires_at>now() RETURNING …` | G-02 |
| `users` / `profiles` | `pg_trgm` GIN index on `users.email` and on `profiles (first_name \|\| ' ' \|\| last_name)` for the admin `query` filter | G-25 |
| `idempotency_keys` (Phase 0) | Per Conv §5, plus `expires_at` (24 h) | G-24 |
| `audit_log` (stub) | `id, actor_user_id, act_user_id NULL, action, subject_type, subject_id, trainer_id NULL, method NULL, path NULL, metadata jsonb, occurred_at`. Never stores email | Plan §1, G-18 |
| `feature_flags` (stub) | `key, trainer_id NULL, enabled`, `UNIQUE (key, trainer_id)` | Plan §1 |

**Global rules**:
- UUIDv7 PKs generated in the app.
- Every FK is `ON DELETE RESTRICT`.
- Postgres native enums. `citext` emails. `timestamptz` timestamps.
- `player_profiles` and `availability_windows` are documented exceptions to Conv §3 "`trainer_id` on
  every tenant table". Their tenancy is defined by `trainer_player_links` (G-10).

**Child account (G-04, G-06)**:
- A child is a `users(role=player)` row referenced by `player_profiles.login_user_id` on an
  `is_dependent` profile.
- The JWT gets `child=true` when such a reference exists. No new column is needed.

**User status lifecycle (G-13)**:

```
register (ShareLink / coach link / camp) → active  (is_email_verified=false; verify is a separate flag)
child login created by parent           → active  (is_email_verified=true; parent vouches)
provision trainer                       → pending (must_change_password=true; cannot log in)
  password/setup with trainer_setup OTT → active
deactivate ⇄ reactivate                 → inactive ⇄ active
erase                                   → deleted (terminal; reactivate ⇒ 409 USER_ERASED)
```

## API Design

Base: Epic-01 §8 as written. **Additions** (each required by behaviour Epic-01 already specifies):

| Method | Path | Role | Purpose |
|---|---|---|---|
| POST | `/api/v1/auth/password/setup` | public | `{token, new_password}`: consumes `trainer_setup`, sets `pending→active`, clears `must_change_password`, returns a token pair (G-03) |
| POST | `/api/v1/me/avatar/confirm` | any | `{avatar_key}`: HEAD check (≤ 5 MB, jpeg/png/webp), then persist (US-01.11) |
| POST | `/api/v1/me/email-change` | any | `{new_email}`: issues an `email_change` OTT to the new address. The old email stays active |
| POST | `/api/v1/me/email-change/confirm` | public | `{token}`: swaps the email. A conflict ⇒ `409 EMAIL_TAKEN` |
| PUT | `/api/v1/me/players/{id}/availability` | player (parent) | Dependent's weekly set. Another owner ⇒ 404 (US-01.09) |
| POST | `/api/v1/trainer/branding/logo` · `/logo/confirm` | trainer | Two-phase logo upload (≤ 2 MB, png/svg+xml/webp). The SVG is sanitised on confirm (US-01.14) |
| GET | `/api/v1/trainer/coaches/{id}/availability` | trainer | Own coach only, else 404 (US-01.10) |

**Changed request shapes**:
- `POST /me/players` and `PATCH /me/players/{id}` accept an optional `login: {email, password} | null`.
  - It creates or replaces the child's login user in the same transaction (active, pre-verified).
  - `login: null` revokes it: the login user is set `inactive` and `login_user_id` is kept for history.
  - Child email must be unique. A conflict ⇒ `409 EMAIL_TAKEN`. (G-04)
- `PATCH /me` accepts the coach public-profile fields (`bio`, `credentials`, `certifications`,
  `is_public`) only for role `coach`. Other roles get 422 (FR-208).

**Documented convention exceptions (G-22)**: `PUT /me/avatar` (presign), and `PUT /me/availability`
plus the dependent variant (full replace).

### Key flows (in addition to spec §11)

```
login:   rate_limit(ip 10/min, email 5/min)
         user = users.get_by_email(email)
         ok = hasher.verify(user.password_hash if user else DUMMY_HASH, pwd)   # constant time
         if not user or not ok or user.status != active → 401 INVALID_CREDENTIALS
         trainer_id = user.id (trainer) | active coach_profile.trainer_id (coach) | None (player)
         child = exists(player_profiles where login_user_id = user.id and is_dependent)
         issue access(15m, jti) + refresh(new family, 30d); last_login_at = clock.now()

refresh: per spec §11. An impersonation-family refresh never extends past
         impersonation_sessions.expires_at (G-14).

coach_unique redemption (unauthenticated, new email only — decision G-12):
         lock share_link FOR UPDATE → invalid/expired/exhausted/inactive → 404 SHARE_LINK_INVALID
         lower(email) != lower(target_email) → 403 LINK_EMAIL_MISMATCH
         email already has an account → 409 WRONG_LINK_KIND
         INSERT users(role=coach, active) + profiles + coach_profiles(is_active=true)
         UPDATE share_links SET use_count=use_count+1 WHERE id=:id AND use_count < max_uses
         INSERT share_link_events(registered) → COMMIT → welcome + verification jobs
coach move (authenticated coach whose current row is inactive): same lock; active elsewhere →
         409 COACH_ALREADY_ASSIGNED {current_trainer_id}; else insert new active row.

trainer deactivated (G-15): its tenant resolves as 404 for player_context, coach principals,
         ShareLink preview/redeem and public branding. No data is changed.
```

**Rate limits** (Conv §4 + G-17): login 10/min per IP and 5/min per email. Password-reset 5/h per
email. Verify-email resend 3/h per user. Register 20/h per IP. Public ShareLink preview 60/min per IP.
Everything else 300/min per principal.

**Caching (G-26)**: `GET /public/trainers/{slug}/branding` sends `Cache-Control: public, max-age=300`.
There is no server cache.

## Error Handling

Domain exceptions (`core/errors.py`) go through one handler to `{"error": {code, message, details}}`
(Conv §5). `IntegrityError` is translated by **constraint name** (never by pre-check):
`uq_users_email` ⇒ `EMAIL_TAKEN`, `uq_trainer_profiles_slug` ⇒ `SLUG_TAKEN`,
`uq_coach_one_active_trainer` ⇒ `COACH_ALREADY_ASSIGNED`, the availability exclusion ⇒
`OVERLAPPING_WINDOWS`.

Codes added by this design (on top of those in Epic-01 §9):

| Code | Status | When |
|---|---|---|
| `WEAK_PASSWORD` | 422 | zxcvbn < 3 or length < 12 |
| `TOKEN_INVALID` | 400 | One-time token unknown, expired or used (one code, no oracle) |
| `IDEMPOTENCY_KEY_REUSED` | 409 | Same key with a different request hash |
| `INVALID_AVATAR` / `INVALID_LOGO` | 422 | Confirm-time content-type or size mismatch, or an SVG with no allowlisted root |

- A pending user logging in gets `401 INVALID_CREDENTIALS`. No account state is revealed.
- Jobs: ARQ retry with exponential backoff, 5 attempts, then a dead-letter structured log (Conv §7).
  Every job re-reads state by id and is guarded by its idempotency key (spec §12).

## Testing Strategy

The tiers follow Conv §8. The test inventory is in the requirements doc (Testing Tasks). Design
decisions:

- **Fakes**:
  - `FakeClock`, `FakeEmailSender` (captures the raw token for flow tests), `FakeObjectStorage`.
  - Recording fakes for `CommitmentChecker`, `PurchaseExecutor` and `CampSubmissionReader`, which can
    be configured to fail.
- **DB fixtures**:
  - testcontainers Postgres 16 and Redis 7.
  - Per-test transaction rollback by default.
  - Concurrency tests use committed data on separate connections, because rollback isolation hides
    races.
- **Factories**: `make_trainer`, `make_coach(trainer)`, `make_player(trainer)`, `make_dependent(parent)`,
  `principal_token(user, act=None)`.
- **Permission matrix as data**:
  - A single `(route, method, role, expected)` table drives the generated 403 cases (every ❌) and
    404 cases (every "own tenant", using a second trainer's ids).
  - A meta-test fails if any registered route is missing from the table.
- **Concurrency**:
  - 20-way `coach_unique` redemption ⇒ 1 success / 19 × 404.
  - 200-way static-link registration ⇒ 200 users, `use_count=200`.
  - 10-way approve ⇒ executor called once.
  - Parallel refresh of the same token ⇒ 1 success + family revoked.
- **Time**: both sides of 15 min / 60 min / 1 h / 24 h / 48 h / 7 d / 30 d. Dependent age on the
  birthday vs the day before.
- **GDPR**: after erasure, scan every text/jsonb column of every table for the original email and
  expect 0 hits.
- **SVG**: script, foreignObject, `on*` handlers, `javascript:` hrefs, XXE and billion-laughs are all
  stripped or rejected.
- **Load** (`tests/load/`, k6): 10k-user seed and one scenario per spec §13 target. A pytest benchmark
  asserts argon2 verify ≥ 100 ms.
- **Merge gates** (`@pytest.mark.gate`, every CI run): the cross-tenant 404 suite and 20-way ShareLink
  redemption.

## Security Considerations

- **AuthN**:
  - argon2id (`t=3, m=64 MiB, p=4`) with dummy-hash verification.
  - HS256 JWT, key from `pydantic-settings` (key rotation via `kid` is post-MVP).
  - Refresh and one-time tokens stored only as SHA-256 hashes.
  - Refresh reuse detection revokes the family.
- **AuthZ**:
  - Role checks as dependencies. Object-level checks in services, 404 across tenants.
  - `trainer_id` always comes from the principal or the validated `player_context`, never from a body.
  - Impersonation: never targets a super_admin, capped at 60 min, every request audited,
    `/admin/*` blocked.
- **Input**:
  - `extra="forbid"` on every request model. `EmailStr`.
  - IANA timezones validated via `zoneinfo`. Hex colour regex.
- **Uploads**:
  - Random keys (`avatars/{uuid7}`, `logos/{uuid7}`).
  - The presigned URL constrains content type and length, and the HEAD check at confirm re-validates.
  - SVGs are re-serialised from the allowlisted tree.
- **Privacy**:
  - structlog redaction by key name. IPs and user agents stored as salted SHA-256.
  - Erasure anonymises per US-01.13 and also scrubs `share_links.target_email`, deletes the user's
    `one_time_tokens`, and relies on `audit_log` never holding emails.
  - `legal_retention_until` = erased_at + config (default 7 years).
- **Abuse and supply chain**: rate limits above. `pip-audit` in CI.

## Open Questions

1. **FR-209**: "Trainer manages own organisation's users". Epic-01 defines no behaviour or endpoints.
   Out of TASK-001 until it is specified.
2. **Spec amendment**: the breached-password clause in §10 is dropped (user decision: zxcvbn only).
   This needs confirming with the spec owner.
3. ~~Dependent age upper bound~~ **Resolved 2026-10-18**: `1 <= age < 18` (spec §10). US-01.03's
   "1–18" is to be corrected upstream.
4. `legal_retention_until`: **resolved as config** `erasure_retention_years`, default 7. The value
   still needs legal sign-off.
7. **Resolved 2026-10-18** (API assumptions): `POST /auth/password/change` is dropped (not in
   requirements), along with the `PASSWORD_CHANGE_REQUIRED` gate it implied. `DELETE /me/players/{id}`
   is a soft removal. The self-guard codes are dropped. The password policy is validated before
   token consumption. `GET /me/approvals` requires `?trainer_id=`. Trainer-provisioning schemas follow
   the US-01.01 example. Details: `specs/api-designer-spec.md` → "Resolved decisions".
8. **Confirmed 2026-10-18**: `POST /auth/password/setup` stays as a separate route. It is not listed
   in spec §8. It is derived from §3 "forced password change on first login", the US-01.01 setup
   token and the §12 `send_trainer_invite` job, and the user chose it over reusing
   `password-reset/confirm`.
5. Spec §14 open questions keep the spec's assumptions: Q-01.05 verification gates purchase and
   ShareLink redemption only, and Q-01.07 refresh TTL is 30 d. Q-01.01 `skill_level` stays nullable
   `text` until decided. Q-01.03 is missing from the spec numbering.
6. Spec text fixes to report upstream: US-01.09's wrong cross-reference (G-21) and the two different
   latency targets (G-20, resolved here as `/me` < 100 ms).
