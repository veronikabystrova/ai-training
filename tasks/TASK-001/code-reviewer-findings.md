# Code Review — TASK-001 Phase E (Tasks 17-20: `/me`, avatar, email change, availability, dependents, contexts, parent approvals)

Scope: `bf77434..20ba920` (`2389dd9`, `d44c3d5`, `dbd9d05`, `d577a91`, `8aa398a`, `20ba920`, and the doc
commit `a586794`). Reviewed against plan Phase E, `specs/api-designer-spec.md` (Domain Me, Dependents,
Approvals, Conventions), and architecture §2 / §5.

## Summary

Phase E is well built and matches the spec closely. Routes are thin. `PATCH /me` and `PATCH /me/players/{id}`
use `model_fields_set` correctly, and an explicit `null` for a NOT NULL column is a 422 rather than a DB error.
The email-change confirm consumes the token, swaps the email and revokes families in one savepoint, so
`uq_users_email` un-consumes the token. Availability replace leaves overlap detection to the GiST constraint, and
the timezone change rolls back with it. Approvals claim the idempotency key, lock the row, run the executor and
store the response in one savepoint, and the 10-way parallel gate test proves there is one effect. The
production executor fails loudly instead of faking success. The gate is green: 410 passed, 98% coverage.

There are no Critical issues. There are **2 Major** issues: dependent availability is stored against the wrong
timezone, and purchases can still be approved after the dependent or trainer link is removed. There are
**8 Minor** issues.

## Issues Found

### Critical (Must Fix)

- None.

### Major (Should Fix)

- [ ] **[Major] Dependent availability writes the parent's timezone, and a child login reads it in a different one** —
  `app/services/availability_service.py:84`, `:100`, `:121`.
  `replace_for_dependent` builds `_Subject(..., timezone_user_id=owner_user_id)`, so
  `PUT /me/players/{id}/availability` with `"timezone": "Asia/Tokyo"` overwrites the **parent's**
  `profiles.timezone`. That silently changes the parent's own `GET /me` and own availability, and every sibling's
  set is reinterpreted. A child login reading the same windows through `GET /me/availability` resolves
  `timezone_user_id=user_id` (the child login's own profile), so it sees the same `HH:MM` values labelled
  with a different zone. Probe: parent PUT Tokyo, then parent `GET /me` → `"Asia/Tokyo"`, and child
  `GET /me/availability` → `{"timezone": "UTC", "windows": [09:00-10:00]}`. The spec stores wall-clock times "in
  the profile timezone", and a dependent has no `profiles` row of its own, so trainer reads (Task 21) will
  inherit the ambiguity. The tests miss it because none of them checks the parent's timezone after a dependent PUT,
  or a child read after a parent write.
  **Fix (needs a design decision):** (a) add `timezone` to `player_profiles` (or to the availability set) and
  use it for every player subject, whether the caller is the owner, the child login or a trainer; or (b) declare that
  a dependent always uses the owner's timezone. Under (b), the child read path must use the owner's profile, and
  the dependent PUT must not silently change the parent's own setting, so either reject a mismatching
  `timezone` or document the side effect in the spec. Add tests for both read paths.

- [ ] **[Major] Pending approvals stay approvable after the dependent is removed or the trainer link is ended** —
  `app/services/approval_service.py:160-169`, `app/services/player_service.py:290-302`, `:328-340`.
  `remove_player` and `remove_trainer` deactivate the profile and links, but they leave the dependent's
  `pending` approvals alone. `decide` checks only the parent, the status and the expiry, so
  `PurchaseExecutor.execute` still runs. Probe: create a pending approval, `DELETE /me/players/{id}` → 204,
  then approve → 200, and the executor ran once. A purchase therefore executes for an inactive profile or
  for a trainer the player is no longer linked to. The spec does not cover this case, so it needs the spec
  owner's decision. Options: (a) in `_apply`, require the profile to be active and the trainer link to be
  active, else 409 `APPROVAL_NOT_PENDING`; (b) move the affected pending approvals to `expired` in
  the same transaction as the removal; (c) accept the behaviour and document it in the Approvals section.

### Minor (Consider)

- [ ] **[Minor] An avatar key is not bound to the user who presigned it** — `app/services/user_service.py:135-147`.
  Any authenticated user can confirm any existing `avatars/<uuid>` key (probe: B confirms A's key → 200).
  The impact is low, since avatars are public through the media origin, but any later per-user cleanup or
  replacement of the old object would break or delete another user's avatar. **Fix:** bind the key to the user,
  either with `avatars/{user_id}/{uuid}` (this changes the spec's `pattern`) or by recording the presigned key
  on the profile (`pending_avatar_key`) and requiring a match at confirm. Ask the spec owner, because the key
  format is in the spec.

- [ ] **[Minor] `DenyRequest` does not forbid extra fields, and a deny without a body is rejected** —
  `app/api/v1/me/schemas.py:354-355`, `app/api/v1/me/approvals.py:301-306`.
  It is the only request model without `ConfigDict(extra="forbid")` (ground rule 4, spec Conventions). The
  probe accepted `{"note": "x", "extra": 1}` with 200. A deny with no body gets 422 `VALIDATION_ERROR`,
  even though `note` is optional. **Fix:** add `extra="forbid"`, and either make the body optional
  (`payload: DenyRequest | None = None`) or state in the spec that `{}` is required.

- [ ] **[Minor] Past-deadline approvals are listed as `pending`** — `app/services/approval_service.py:73-93`.
  Until the expiry job (Task 25) runs `expire_due`, `GET /me/approvals?status=pending` returns rows whose
  `expires_at` has passed, and approving one returns 409 `APPROVAL_NOT_PENDING`. **Fix:** report
  `expired` in `approval_view` when `status is pending and expires_at <= now`, and apply the same
  condition to the `status` filter. Alternatively, note the job dependency in the plan's open points.

- [ ] **[Minor] The "argon2 outside the transaction" comment is not accurate** — `app/services/player_service.py:223-224`, `:262-277`.
  By the time `_hash_login` runs, the preceding SELECTs have already autobegun a transaction, so a
  pooled connection is held for the whole Argon2 hash. No row locks are held, so this is a
  pool-pressure concern only. **Fix:** commit (end the read transaction) before hashing, as the auth flows do,
  or correct the comment.

- [ ] **[Minor] Untested branches on error paths** — `app/services/player_service.py:437-440`
  (replacing an existing child login with a taken email → 409 `EMAIL_TAKEN`, a spec'd status for
  `PATCH /me/players/{id}`), `app/services/approval_service.py:79-80` (the `TOKENS` currency mapping).
  **Fix:** add one API test for each.

- [ ] **[Minor] Child logins are created pre-verified for any address the parent types** —
  `app/services/player_service.py:395-404`, `:430-436`. This follows the spec ("pre-verified"), but it has a
  side effect. A typo or a foreign address both squats that email (the real owner gets `ACCOUNT_EXISTS`) and lets
  whoever holds the mailbox take over the child login through `POST /auth/password-reset/request`.
  **Fix:** confirm the risk with the spec owner. Either accept and record it, or send a verification email
  to the child address and keep `is_email_verified=False` until it is confirmed.

- [ ] **[Minor] One query per trainer id** — `app/services/player_service.py:207-208`. `create_player`
  runs `has_active_link_for_owner` once per `trainer_ids` entry (up to 20). **Fix:** use one
  `trainer_id IN (...)` query that returns the linked set, and return 404 if any id is missing.

- [ ] **[Minor] Small hygiene items** —
  (1) `app/api/v1/me/routes.py:46,48`: `_by_principal = by_principal` and `_ERRORS = ERRORS` are
  aliases that add nothing.
  (2) `app/api/v1/me/approvals.py:245`: `Depends(require_parent)` in `dependencies=` duplicates the
  `ParentDep` parameter.
  (3) `app/services/player_service.py:404`: the magic `first_name[:100]` is dead, because
  `display_name` already has `max_length=100`.
  (4) `app/db/alembic/versions/0002_identity.py`: `player_profiles.is_active` was added by editing
  `0002` in place. This is fine while `0002` has never been applied outside this branch. Confirm that before
  merge, or move the change to a `0003` migration.

### Positive Notes

- `UpdateMe` / `UpdatePlayer` handle absent, `null` and value correctly. `FIELD_NOT_ALLOWED` lists each
  offending field, and the three states of `login` (absent, `null`, object) behave as the spec describes:
  revoking a login keeps `login_user_id` and revokes refresh families.
- Ownership misses are 404 everywhere (`get_owned`, `lock_by_id_for_parent`, `has_active_link_for_owner`),
  and "unknown, inactive and unrelated trainer" cannot be told apart, so there is no oracle.
- The email change reuses the Phase D token design: issuing supersedes earlier tokens, the outcome is
  "token invalid" for wrong-purpose, expired and inactive-user tokens alike, and the confirm commits the
  revoke and the audit row together.
- Approvals: the idempotency hash covers method, path and body, so reusing a key across approvals or with a
  different note is a 409. The replay comes from the stored JSON, so fresh and replayed bodies are identical,
  and a failed executor leaves no claim behind.
- `S3ObjectStorage` now creates its client lazily and under a lock, outside app construction.
  `rate_limit_overrides` keeps the production defaults untouched while it lets the tests shrink the limits.
- The tests are thorough (814 lines for dependents alone), and the API, service and concurrency layers are
  each tested. There is no `# type: ignore` in the codebase.

## Verification Evidence (DoD Standard tier, run in this review)

| Check | Command | Result |
|---|---|---|
| Lint | `uv run ruff check .` | PASS |
| Format | `uv run ruff format --check .` | PASS (142 files) |
| Types | `uv run mypy app tests` | PASS (142 files, 0 issues) |
| Tests | `uv run pytest -q --cov=app` | PASS: 410 passed; 98% total. `api/v1/me/*` 100%, `user_service` 96%, `player_service` 96%, `availability_service` 95%, `approval_service` 98% |
| Warnings | pytest summary | 3 testcontainers deprecation warnings only |
| Naming | `ls tasks/TASK-001` | PASS: all skill-prefixed |
| Committed | `git status` | PASS: clean before this review file |

Probes were run in a temporary test file, which was deleted afterwards:
(1) parent PUT of dependent availability with Tokyo → parent `/me.timezone` = `Asia/Tokyo`, child reads `UTC`;
(2) user B confirms user A's avatar key → 200;
(3) remove dependent → approve its pending approval → 200, executor ran;
(4) deny without body → 422; (5) deny with an unknown field → 200.

---

# Code Review — TASK-001 Phase D (Tasks 13-16: login/refresh/logout, password reset + verification + setup, register, impersonation exit)

Scope: `63adf49..719e953` (`f0c1a86`, `fcd630e`, `e512e9c`, `ed9cc2a`, `719e953` and the doc commits
`5beef36`, `f422b6f`). Reviewed against plan Phase D, `specs/api-designer-spec.md` (`/auth/*` sections
and Conventions), architecture §2 / §5, and the brainstorm login / refresh / coach_unique flows.

## Summary

Phase D is solid. The services own their transactions. Refresh rotation locks the row
(`FOR UPDATE`), commits the family revoke and its audit row *before* it raises
`REFRESH_REUSE_DETECTED`, and caps impersonation families at the session's `expires_at` (G-14).
Share-link redemption locks the link, hashes the password *outside* the lock, and turns duplicate
emails and second active tenures into flow-specific 409s using savepoints rather than pre-check
SELECTs. Password policy runs before the token is consumed, so a weak password leaves the link usable.
Raw tokens never reach logs. The Phase C carry-overs (`hide_parameters`, greenlet coverage,
`WWW-Authenticate: Bearer`) are fixed. The gate is green: 289 passed, 98% coverage.

There are no Critical issues. There are **2 Major** issues: one rate-limit gap that breaks the spec,
and one spec contradiction on the authenticated register body. There are **10 Minor** issues.

## Issues Found

### Critical (Must Fix)

- None.

### Major (Should Fix)

- [ ] **[Major] `POST /auth/password-reset/request` has no default per-IP rate limit** —
  `app/api/v1/auth/routes.py:161-172`.
  Spec Conventions (line 20) and the architecture `rate_limit` row say that routes without a
  principal fall back to the default limit keyed by client IP. Every other public auth route has a
  limit (`login_ip`, `register_ip`, or `_default_by_ip`). This one has only the service-side 5/h
  *per email*. One client can therefore spray any number of addresses, and each new address issues
  a token row and enqueues an email job (mail flooding, write amplification, unbounded load).
  **Fix:** add `dependencies=[_default_by_ip]` to the route, plus a test that the 301st request
  from one IP within a minute, using distinct emails, returns 429.

- [ ] **[Major] Authenticated redemption requires a body the spec says is ignored** —
  `app/api/v1/auth/schemas.py:88-100`, `app/api/v1/auth/routes.py:271-296`.
  The spec says "Authenticated existing user ⇒ link only, body ignored except `player_profile_ids`".
  Yet `RegisterRequest` requires `email`, `password` (≥ 12 chars), `first_name` and `last_name`. A
  client that follows the prose and sends `{"player_profile_ids": [...]}` (or `{}`) gets 422.
  The tests hide this: every authenticated case sends a full dummy body
  (`tests/api/auth/test_register.py:451,486,505`). The spec itself is inconsistent, so this needs
  the spec owner's decision, not a unilateral edit. Options:
  (a) make the identity fields optional in the schema and require them in the service's
  unauthenticated branch (422 with field details), then update the spec block;
  (b) keep the schema and change the spec prose to "the full body is required; only
  `player_profile_ids` is used". Add an authenticated test with a minimal body for whichever
  option is chosen.

### Minor (Consider)

- [ ] **[Minor] Timing oracle on password-reset request** — `app/services/auth_service.py:231-244`.
  An unknown or inactive email returns right after one SELECT. An active user adds an UPDATE, an
  INSERT, a COMMIT and an enqueue. That difference is measurable and undermines the "202 always,
  no enumeration" contract. **Fix:** enqueue one job keyed on the email and let the worker do the
  lookup and issue the token. Alternatively, accept and document the residual risk.

- [ ] **[Minor] A benign refresh retry logs the user out** — `app/services/auth_service.py:154-168`.
  Two tabs refreshing together, or a client retrying after a lost response, look like reuse. The
  family is revoked, including the winner's successor (`tests/services/test_auth_refresh.py`
  asserts exactly this). It matches the spec's literal reading, but it is a UX decision.
  **Fix:** confirm with the spec owner. If unwanted, allow a short grace window (for example
  10 s after `consumed_at`) that returns 401 `INVALID_REFRESH_TOKEN` without revoking.

- [ ] **[Minor] Spec wording for a rate-limited reset request contradicts the code** —
  `specs/api-designer-spec.md` §`POST /auth/password-reset/request` says "the limited response is
  also 202 for unknown emails". The code returns 429 for known and unknown emails alike, which is
  the oracle-free choice, and it is tested (`test_reset_request_rate_limited_per_lowercased_email`).
  **Fix:** update the spec sentence to "429 for any email once the per-email limit is hit (no
  oracle)". Ask the spec owner first.

- [ ] **[Minor] Reset and setup check password strength without user context** —
  `app/services/auth_service.py:249,302` versus `app/services/sharelink_service.py:149`.
  Register passes `[email, first_name, last_name]` to zxcvbn. Reset confirm and trainer setup pass
  nothing, so a password built from the user's own email scores higher there.
  **Fix:** look up the token's user read-only (no consume) before `validate`, or accept the
  difference and say so in a comment.

- [ ] **[Minor] `LogoutRequest.refresh_token` has no length bounds** —
  `app/api/v1/auth/schemas.py:26-29`. `RefreshRequest` bounds the same value (20..200), but logout
  hashes an unbounded string. **Fix:** `Field(min_length=20, max_length=200)`. This is a spec gap
  as well, so mirror it in the spec block.

- [ ] **[Minor] `current_principal_any_tenure` / `AnyTenurePrincipalDep` are unused** —
  `app/api/deps.py:320-331,345` (the only uncovered block in deps). Only the optional variant is
  wired. **Fix:** either remove the required variant from the code and the architecture §2 table
  (YAGNI), or cover it with a probe-route test.

- [ ] **[Minor] The camp-submission branch is a silent 404 stub that is not recorded in spec or plan** —
  `app/services/sharelink_service.py:133-135`, `app/api/v1/auth/routes.py:279-280`.
  `?camp_submission_id=<uuid>` always returns 404 `NOT_FOUND`. The docstring cites Epic-08, but
  the plan's "Open points" and the spec do not mention it. **Fix:** add it to the plan's open
  points. Consider 501 or a distinct code so clients can tell "not built" from "unknown submission".

- [ ] **[Minor] Jobs enqueued after commit can be lost** —
  `app/services/sharelink_service.py:251-256`, `app/services/auth_service.py:243-244,294-295`.
  If `enqueue` fails, the client gets a 500 even though the account or token is committed. A
  register retry then hits `ACCOUNT_EXISTS`, and the user never receives a verification email
  (resend is the only recovery). Acceptable while `LoggingJobQueue` is the adapter.
  **Fix:** in Task 25, use an outbox row written in the same transaction, or catch and log enqueue
  failures after commit instead of returning 500.

- [ ] **[Minor] Re-redeeming a static link consumes a use with no new link** —
  `app/services/sharelink_service.py:306`. `upsert_active` is idempotent, but `_consume_use`
  always increments. A player who is already linked can exhaust a `max_uses` static link.
  **Fix:** have `upsert_active` report whether it inserted or reactivated, and skip the increment
  and event when nothing changed. Alternatively, document that every redemption counts.

- [ ] **[Minor] Small hygiene items** —
  (1) `app/api/deps.py:293`: `Principal.child` comes from the token claim, while the dataclass
  comment (`:66`) says it is DB-derived. It is the only `CHILD_CANNOT_REDEEM` gate, and access
  tokens last 15 min, so the risk is low; fix the comment, or read it in `_resolve`.
  (2) `app/services/sharelink_service.py:231`: magic `[:120]`; use the column length.
  (3) `app/api/v1/auth/routes.py:266`: `EMAIL_TAKEN` is documented but unreachable from register
  (all duplicates map to `ACCOUNT_EXISTS` / `WRONG_LINK_KIND`), and the spec lists it too.
  (4) `routes.py:297-303` builds a `JSONResponse` for the 200 branch, while `verify_email_resend`
  sets `response.status_code`. Pick one pattern.

### Positive Notes

- Reuse detection is correct under concurrency. The row lock serialises the racers, and the revoke
  and its audit row commit before the raise. A gate test proves one winner, a single audit row,
  and that the successor dies.
- Refresh rebuilds claims from the DB (A-03). Impersonation families keep `act`/`jti`, are capped
  at the session expiry, and are revoked if the session ended or the admin was deactivated. That
  closes the Phase C "deactivated admin" minor for refresh.
- Login is oracle-free: it always verifies against a dummy hash, returns one 401 for all five
  states, upgrades the hash on `needs_rehash`, and lowercases the rate-limit key (citext lookup).
- The token-first design is applied consistently: policy and argon2 run before `consume`, the lock
  is never held across hashing, and token failures share one code and message.
- Share-link redemption uses savepoints to map `uq_users_email` and
  `uq_coach_one_active_trainer` into `ACCOUNT_EXISTS` / `WRONG_LINK_KIND` /
  `COACH_ALREADY_ASSIGNED`(with `current_trainer_id`) without pre-check SELECTs (ground rule 5).
  The 20-way and 200-way concurrency tests prove there is no overshoot and no lost writes.
- `any_tenure` is narrowly scoped to `/auth/register`, documented in the architecture table, and
  the G-15 adjustment does not change behaviour for any other route.
- `LoggingJobQueue` logs only the job name, never kwargs that hold raw tokens. IP and UA are stored
  only as salted hashes.
- Route handlers are thin, and every request model has `extra="forbid"`.

## Verification Evidence (DoD Standard tier, run in this review)

| Check | Command | Result |
|---|---|---|
| Lint | `uv run ruff check .` | PASS |
| Format | `uv run ruff format --check .` | PASS (122 files) |
| Types | `uv run mypy app tests` | PASS (122 files, 0 issues) |
| Tests | `uv run pytest -q --cov=app` | PASS: 289 passed, 1 xfailed (strict, `/me` until Task 17); 98% total. `auth/routes.py` 100%, `auth_service.py` 99%, `sharelink_service.py` 97%, `impersonation_service.py` 100% |
| Warnings | pytest summary | 3 testcontainers deprecation warnings only |
| Naming | `ls tasks/TASK-001` | PASS: all skill-prefixed |
| Committed | `git status` | PASS: clean before this review file |

---

# Code Review — TASK-001 Phase C (Tasks 11-12: `api/deps.py`, middleware, app factory)

Scope: commit `a7fe73e` (Tasks 11-12), plus `cbe8a28` (gender enum) and `3b18314` (plan sync).
Reviewed against plan Phase C, architecture §2 / §5 / §6, `specs/api-designer-spec.md` (Conventions:
Idempotency) and the Phase B query rules.

## Summary

Phase C follows architecture §2 closely and is well built. Every dependency in the table exists
with the specified signature and failure codes. `_resolve` is one indexed statement that uses the
bare `cp.is_active` and binds `jti` as a UUID, and a test guards both rules
(`test_principal_resolution_is_one_select_without_is_true`). Cross-tenant and player-context misses
are 404, never 403. `SuperAdminScope` rejects `act`. There is now a single 500 path that logs once
with the correlation id. The idempotency claim moved into the service transaction (`IdempotencyGuard`),
which is the right choice for concurrency, and the docstring explains it. The gate is green.

There are no Critical issues. There are **2 Major** issues, both in the logging and audit paths
around the request. There are **6 Minor** issues.

## Issues Found

### Critical (Must Fix)

- None.

### Major (Should Fix)

- [ ] **[Major] Unhandled DB errors log bound SQL parameters (emails, password/token hashes)** —
  `app/db/session.py:18` (`create_engine`), surfaced by `app/api/middleware.py:39` and
  `app/core/errors.py` (`unmapped_integrity_error`).
  `str()` of any SQLAlchemy `StatementError` appends `[parameters: (...)]`. I confirmed this locally:
  an `IntegrityError` on an `INSERT INTO users` prints the email and the argon2 hash. The new single
  500 path logs `exc_info=True`, and `format_exc_info` runs *after* `redact_processor`. Key-name
  redaction therefore never sees the traceback text, so the "logged once, redacted" guarantee
  (Task 12, architecture §5 Privacy) does not hold for DB failures, which are the most common 500.
  **Fix:** `create_async_engine(url, pool_pre_ping=True, hide_parameters=True)`, plus a test that
  raises an `IntegrityError` with parameters through `/boom` and asserts they are absent from
  `capture_logs()` output.

- [ ] **[Major] Impersonation audit is not reliable for "every act-bearing request"** —
  `app/api/middleware.py:53-72`.
  1. If the route raises an unhandled exception, `call_next` re-raises (that is how
     `BaseHTTPMiddleware` works). The audit block is skipped, so an impersonated request that
     crashed, possibly after a partial write, leaves no `audit_log` row (NFR-006).
  2. The audit row is written in a *separate* session after the handler has committed. The
     docstring says "fail closed", but a failing audit insert turns an already-committed mutation
     into a 500. The client then retries a write that succeeded, and nothing records who made the
     first one.
  **Fix:** wrap `call_next` in `try/finally` (or `except` + re-raise) so the audit row is written
  on the exception path too, and correct the docstring to "fail visible, not atomic". Alternatively,
  write the row inside the request transaction from `current_principal`/the service, and leave the
  middleware to set only the header. Add a test with an impersonated probe route that raises.

### Minor (Consider)

- [ ] **[Minor] Coverage under-reports async DB code** — `pyproject.toml` (no `[tool.coverage.run]`).
  `app/api/deps.py:204-238` (all of `_authenticate` after the query) and
  `app/services/idempotency_service.py:50-58` (the replay path) show as uncovered, yet tests
  exercise both (`test_coach_moved_tenant_token_is_stale`,
  `test_idempotency_replays_stored_response_and_runs_once`). Coverage loses the trace after
  SQLAlchemy's greenlet switch. The 96% figure is therefore unreliable in both directions.
  **Fix:** `[tool.coverage.run] concurrency = ["greenlet", "thread"]`.

- [ ] **[Minor] Duplicate `get_session`** — `app/db/session.py:25` vs `app/api/deps.py:75`.
  The `db` version is now used only by `tests/db/test_session.py`.
  **Fix:** delete it (and its test), or have `deps.get_session` delegate to it.

- [ ] **[Minor] IP rate limit keys on `request.client.host` with no proxy story** —
  `app/api/deps.py:381`. Behind a load balancer every client shares one bucket (login 10/min
  becomes global), and the `"unknown"` fallback is also a shared bucket. Nothing in `specs/` or the
  plan defines the deployment.
  **Fix:** record the decision. Either run uvicorn with `--proxy-headers --forwarded-allow-ips=<LB>`,
  or add a trusted-proxy setting. Raise it as an open question rather than adding config unasked.

- [ ] **[Minor] Correlation-id regex accepts a trailing newline** — `app/api/middleware.py:17,33`.
  `$` matches before a final `\n`. The HTTP parser rejects such header values today, so this is
  defence in depth only. **Fix:** `_VALID_CORRELATION_ID.fullmatch(supplied)` and drop `^`/`$`.

- [ ] **[Minor] Impersonation stays valid if the admin is deactivated** — `app/api/deps.py:148-155`.
  The join checks the session's `jti`/target, but not that the impersonating admin
  (`imp.admin_user_id`) is still `active`. The session is bounded at 60 min, and architecture §2
  does not require this check. **Fix:** decide with the spec owner. If wanted, it is one more
  `LEFT JOIN users a ON a.id = imp.admin_user_id`.

- [ ] **[Minor] 401s carry no `WWW-Authenticate: Bearer` header** — `app/api/deps.py:178,248`.
  RFC 6750 expects it, and some clients rely on it. Low priority; only add it if the spec's error
  contract allows.

### Positive Notes

- The revalidation query matches §2 exactly, and a statement-capturing test locks in the
  `IS true` and one-round-trip rules.
- The coach `tid` mismatch is 401 and a deactivated tenant is 404. Both are tested, along with
  impersonation of another target, ended/expired sessions and `act` without a session.
- `player_context` builds `player_profile_ids` in one query (parent sees dependents, child sees only
  itself), filters on an active trainer, and every miss is 404.
- `rate_limit` refuses email-keyed classes at import time, so a misuse cannot reach production.
- Dependencies end their read transactions explicitly, so services own their transaction. The
  module docstring documents this.
- The correlation id is validated before it is echoed or logged, and the single 500 path has a test
  showing it logs exactly once.
- Ports and fakes are recording and configurable to fail, as the plan specified.

## Verification Evidence (DOD Standard tier)

| Check | Command | Result |
|---|---|---|
| Lint | `uv run ruff check .` | PASS |
| Format | `uv run ruff format --check .` | PASS (106 files) |
| Types | `uv run mypy app tests` | PASS (106 files, 0 issues) |
| Tests | `uv run pytest -q --cov=app` | PASS: 178 passed, 1 xfailed (strict, `/me` until Task 17), 96% (under-reported, see Minor #1) |
| Warnings | `pytest -W default` | testcontainers deprecations; `ResourceWarning` unclosed Redis connection in `test_principal_rate_limit_is_per_user` (test hygiene only) |
| Naming | `ls tasks/TASK-001` | PASS: all skill-prefixed |
| Committed | `git status` | PASS: clean |

---

# Code Review — TASK-001 Phase B (Tasks 9-10: models, migration `0002_identity`, repositories)

Scope: commits `9289861` (asyncpg constraint name), `c4562ed` (rate limiter fail-open), `41c50e2`
(Task 9: models, migration, factories, drift test) and the **uncommitted** Task 10 working tree
(`app/repositories/`, `tests/repositories/`). Reviewed against the plan (Tasks 9-10), architecture
§2-§3, requirements "Models" / "Repositories" tables, and GOLDEN-PRINCIPLES.md.

## Summary

Phase B is in good shape. The schema matches architecture §3 (constraint names, partial indexes,
GiST `EXCLUDE`, RESTRICT FKs, native enums). Model/migration drift is guarded by a test. All three
Phase A carry-overs are done (`target_metadata`, `nulls_not_distinct`, rate-limiter fail-open), and
so is the asyncpg `constraint_name` fix. The repositories follow the tenancy contract, and the
OTT/refresh/approval semantics are correct and well tested. The gate is green: 116 tests and 95% coverage.

There are no Critical issues. There are **3 Major** issues. Two are performance bugs that I
confirmed with `EXPLAIN`: the hot-path coach lookups and the admin directory search never use the
indexes built for them. The third is an untested repository. There are **10 Minor** issues.

## Issues Found

### Critical (Must Fix)

- None.

### Major (Should Fix)

- [ ] **[Major] `is_active.is_(True)` bypasses both partial indexes on `coach_profiles`** —
  `app/repositories/coach_profile.py:26,35,43,55`.
  SQLAlchemy renders `is_active IS true`. Postgres cannot prove that this implies the index predicate
  `WHERE is_active`, so `uq_coach_one_active_trainer` and `ix_coach_trainer_active` are never used.
  Verified with `SET enable_seqscan=off`: the query falls back to `Seq Scan on coach_profiles` (cost 1e10).
  With a bare `AND is_active` it uses `Index Scan using uq_coach_one_active_trainer`. `get_active_for_user`
  resolves the tenant for coach principals, and downstream epics validate coaches with `get_active`.
  Both sit on the per-request auth path (architecture §2: one indexed round trip, `GET /me` p95 < 100 ms),
  and the cost grows with the history of tenure rows.
  **Fix:** use the bare boolean column, e.g. `.where(CoachProfile.user_id == user_id, CoachProfile.is_active)`,
  in all four places. Do the same in the Task 11 revalidation query (`cp.is_active`, not `IS TRUE`).
  Add a regression test that runs `EXPLAIN` with `enable_seqscan=off` and asserts the index name is in the plan.

- [ ] **[Major] The admin directory `query` filter cannot use either trigram index (G-25)** —
  `app/repositories/user.py:61-64`.
  There are two problems:
  1. `User.email.ilike(...)` on a `CITEXT` column resolves to citext's own `~~*` operator, so it does
     not match `ix_users_email_trgm`, which uses the text opclass `gin_trgm_ops`. Verified:
     `email ILIKE '%abc%'` gives a Seq Scan, while `email::text ILIKE '%abc%'` gives `Bitmap Index Scan on ix_users_email_trgm`.
  2. Even after that fix, `OR` across two joined tables (`users.email … OR profiles.name …`) cannot
     become a BitmapOr, so the planner still filters the whole join.

  The name expression on its own does match `ix_profiles_full_name_trgm` (verified).
  **Fix:** `cast(User.email, Text).ilike(pattern, escape="\\")`, and resolve the match set as a union of
  two indexable subqueries:
  `User.id.in_(union(select(User.id).where(email_text.ilike(p)), select(Profile.user_id).where(full_name.ilike(p))))`.
  Cover it with an `EXPLAIN` test like the one above.

- [ ] **[Major] `ShareLinkEventRepository` has no tests (0% coverage)** — `app/repositories/share_link_event.py`.
  It is in the requirements "Repositories" table and needed by the share-link flow (Task 21), but no test
  exercises it. This fails the DoD Standard item "new code has test coverage (at least happy path)".
  **Fix:** add `tests/repositories/test_share_link_event.py`. It should test that `append` persists `opened` and
  `registered`, and that an unknown `kind` raises `IntegrityError` on `ck_share_link_events_kind`.

### Minor (Consider)

- [ ] **[Minor] Task 10 is not committed** — `git status`: `?? app/repositories/`, `?? tests/repositories/`.
  This fails the DoD Minimum item. Commit it per plan Step 5 (`feat(repositories): …`) once the fixes above land.
- [ ] **[Minor] `ImpersonationSession.jti` is `String(64)`, but `Principal.jti` is `UUID`** (architecture §2) —
  `app/models/identity.py:327`, `app/repositories/impersonation.py:19,34,40`. The Task 11 revalidation join
  `s.jti = <token jti>` will pass a `UUID` to a varchar column, which asyncpg rejects ("expected str").
  The alternative is `str()` calls scattered across the code. **Fix:** make the column `Uuid` (model and
  migration) and type the repository parameters as `UUID`, before any data exists.
- [ ] **[Minor] 10 new `# type: ignore[attr-defined]` on `result.rowcount`** — `user.py:82`, `coach_profile.py:59`,
  `trainer_player_link.py:90`, `share_link.py:77,89`, `impersonation.py:47`, `refresh_token.py:75,79`,
  `one_time_token.py:80`, `idempotency.py:69`. The Phase A review recorded zero ignores in the codebase, and
  GOLDEN-PRINCIPLES says to fix the type. **Fix:** add one helper, e.g. in `app/db/session.py`:
  `def rowcount(r: Result[Any]) -> int: return cast("CursorResult[Any]", r).rowcount`.
- [ ] **[Minor] The directory query inner-joins `profiles`** — `app/repositories/user.py:49`. No DB constraint
  guarantees a 1:1 profile, so a user without one (for example a seeded super_admin) silently disappears
  from the admin directory. **Fix:** use `outerjoin`, or make profile creation part of the user-creation
  invariant and assert it in a test.
- [ ] **[Minor] `OneTimeTokenRepository` accepts `OttPurpose | str`** — `app/repositories/one_time_token.py:28,34,59,67`.
  `OttPurpose("bogus")` raises a bare `ValueError` (a 500), and the union is primitive obsession.
  **Fix:** accept only `OttPurpose`. Callers are internal services.
- [ ] **[Minor] `ShareLinkEvent.kind` is a free `str` plus a CHECK**, while every other categorical column is a
  native enum (architecture §3: "native Postgres enums") — `app/models/identity.py:266`,
  `app/repositories/share_link_event.py:16`. At least type it as a `StrEnum` or `Literal["opened", "registered"]`
  on the Python side. The same applies to `AvailabilityWindow.subject_type` (`:286`).
- [ ] **[Minor] `_tstz()` and `_fk()` return bare `Any`** — `app/models/identity.py:85,89`. Return `DateTime` and
  `ForeignKey` (GOLDEN-PRINCIPLES: no bare `Any`).
- [ ] **[Minor] Relationships use the default lazy loading under `AsyncSession`** — `app/models/identity.py:117,139,179`.
  If `user.profile` or `coach.user` is touched outside an eager load, it fails with `MissingGreenlet`, far
  from the cause. **Fix:** `lazy="raise"` so a missing `selectinload`/`contains_eager` fails clearly.
- [ ] **[Minor] The drift test does not compare server defaults** — `tests/db/test_metadata_matches_migrations.py:188`.
  Only `compare_type` is on, so default drift goes undetected (for example, `certifications` is `"[]"` in the
  migration and `'[]'::jsonb` on the model). **Fix:** add `compare_server_default=True`, normalising where
  Alembic compares textually.
- [ ] **[Minor] Test gaps** —
  (a) `test_directory_keyset_pages_cover_all_rows_in_order` (`tests/repositories/test_user.py:24`) creates every
  row in one transaction. `now()` is therefore identical for all of them, so only the `id` tie-breaker is tested.
  Add a case with explicit, distinct `created_at`.
  (b) The `0002` downgrade is not covered (`0002_identity.py:381-399`, 0%). It passed when probed manually in
  this review (`0001 → head → base → head`); add it next to `test_baseline_migration.py` as plan Task 9
  Step 4 intended.
  (c) The `add()` methods on the User, Profile, TrainerProfile, Coach and Player repositories are untested
  because the factories bypass them.

### Phase A carry-overs: status

| Carry-over | Status |
|---|---|
| `target_metadata` = `Base.metadata` with all models, plus `platform.py` mirroring `0001` (`metadata_` mapping) | Done. `test_models_match_migrations` passes (no diff) |
| `FeatureFlag` `postgresql_nulls_not_distinct=True` | Done (`app/models/platform.py:64-69`) |
| Rate limiter fail-open on Redis outage, warning without key/PII | Done, with tests for the outage path and that the real limit still raises |
| Naive-datetime cursor rejected, `CursorQ max_length=256` | Done (`app/core/pagination.py:19,30`) |

### Positive Notes

- The schema follows architecture §3 exactly: `ck_coach_left_iff_inactive`, a partial unique index as the
  coach-move concurrency guard, the GiST `EXCLUDE` with `'[)'` (touching windows allowed and tested),
  per-kind share-link CHECKs, a brand-hex CHECK, `CITEXT` emails, and RESTRICT on every FK. Constraint names
  are explicit and stable, which keeps `map_integrity_error` reliable.
- `constraint_name()` handles both drivers, and a real-DB test (`test_real_asyncpg_error_maps_to_domain_error`)
  proves the mapping, closing a bug where no real error ever matched.
- The OTT repository matches §3.2 to the letter. Issuing supersedes open tokens. Consumption is one atomic
  `UPDATE … RETURNING`, with purpose always in the predicate and one `None` for unknown, expired or used
  (no oracle).
- Tenancy is enforced structurally. Tenant-scoped methods take a required keyword-only `trainer_id`, and a
  signature meta-test with an explicit, commented exception list (`lock_by_code`, `lock_by_id_for_parent`,
  `expire_due`) keeps it that way. `increment_use` is also tenant-scoped, which is stricter than the plan.
- Concurrency primitives are correct: `FOR UPDATE` plus `populate_existing` on lock methods (with a test that
  captures the SQL), `ON CONFLICT DO UPDATE … RETURNING` for an idempotent link upsert, and
  `ON CONFLICT DO NOTHING` for idempotency-key reservation.
- The `LIKE` metacharacters in the directory query are escaped, and a test (`query="%%"` returns nothing)
  proves it.
- Repositories never commit. All time comes from an injected `now`, never `datetime.now()`.

## Verification Evidence (DoD Standard tier, run in this review)

`uv run` still fails here (`pyenv: version '3.12' is not installed`), so the gate was run with the project
venv binaries (Python 3.12.13):

| Check | Command | Result |
|---|---|---|
| Lint | `.venv/bin/ruff check .` | All checks passed |
| Format | `.venv/bin/ruff format --check .` | 72 files already formatted |
| Types | `.venv/bin/mypy app tests` | Success: no issues found in 72 source files |
| Tests | `.venv/bin/pytest -q --cov=app` | **116 passed**, 3 warnings (testcontainers deprecation, third-party) |
| Coverage | `--cov-report=term-missing` | 95% total; `share_link_event.py` 0%, `profile.py` 76%, `0002` downgrade uncovered |
| Migration round-trip | throwaway probe: `downgrade 0001 → upgrade head → downgrade base → upgrade head` | passes |
| Query plans | throwaway probe: `EXPLAIN` with `enable_seqscan=off` on the testcontainer DB | see Major #1 and #2 |
| TODO/FIXME | `grep -rn "TODO\|FIXME\|HACK" app tests` | none |
| File naming | `tasks/TASK-001/*` | all skill-prefixed |
| Uncommitted | `git status` | **FAIL**: Task 10 untracked (Minor #1) |

The probes lived in the session scratchpad. Nothing was added to the repository.

---

# Code Review — TASK-001 Phase A, re-review of the Major fixes

Scope: the fixes for the 4 Major findings from the first Phase A review (kept below for history).
Files: `app/core/errors.py`, `app/core/config.py`, `app/core/svg.py`,
`app/db/alembic/versions/0001_baseline.py`, plus `tests/core/test_errors.py`, `tests/core/test_svg.py`,
`tests/core/test_ids_clock_config.py` and `tests/db/test_baseline_migration.py`.

## Summary

All 4 Major findings are fixed and each one has a regression test that fails without its fix. No
new Critical or Major issues. There are 5 new Minor items. Most are follow-ups that later tasks
need to know about: the new error codes are not in the spec's catalog, a framework-raised 401 gets a
different code from the spec's, the 500 traceback is logged twice, the depth limit has no boundary
test, and the ORM model must repeat the NULLS NOT DISTINCT setting. Lint, format, mypy and all
43 tests pass. The work is still uncommitted.

## Status of the previous Major findings

| # | Finding | Status | Evidence |
|---|---|---|---|
| 1 | Error envelope gaps (404/405/500) | **Fixed** | `errors.py:132-148`: a `StarletteHTTPException` handler (keeps `exc.headers`, so `Allow` survives on 405) and a catch-all `Exception` handler. Unmapped `IntegrityError` now logs and returns the 500 envelope (`:119-121`) instead of re-raising. Tests: `test_unknown_route_and_wrong_method_use_envelope`, `test_unhandled_errors_return_500_envelope[/boom,/integrity]` |
| 2 | `feature_flags` allows duplicate global flags | **Fixed** | `0001_baseline.py:68-73` `postgresql_nulls_not_distinct=True` (SQLAlchemy 2.1.5, PG 16). Test `test_feature_flags_global_key_is_unique` inserts a second `(x, NULL)` row inside a SAVEPOINT and expects `IntegrityError` |
| 3 | Secrets not protected | **Fixed** | `config.py:16` `jwt_secret: SecretStr = Field(min_length=32)`; `:25` `hash_salt: SecretStr = Field(min_length=16)`, required, with no default. Test checks that a short secret and a missing salt are rejected, and that the secret is not in `repr`. No production code reads these yet, so `.get_secret_value()` is only needed once `TokenIssuer` is wired up from `Settings` |
| 4 | Deep SVG causes `RecursionError` | **Fixed** | `svg.py:21,43-45,58` `MAX_DEPTH = 32` is checked before the element filter, so dropped subtrees still count toward the depth. Test: 5,000 nested `<g>` → `422 INVALID_LOGO` |

## Issues Found

### Critical (Must Fix)
- None.

### Major (Should Fix)
- None.

### Minor (Consider)

- [ ] **[Minor] The new error codes are not in the spec's catalog** — `app/core/errors.py:92-106,134`.
  `specs/api-designer-spec.md:92-93` lists the shared codes as `INVALID_TOKEN, IMPERSONATION_EXPIRED,
  FORBIDDEN, NOT_FOUND, VALIDATION_ERROR, RATE_LIMITED`. The fix adds `METHOD_NOT_ALLOWED`,
  `INTERNAL_ERROR`, `UNAUTHORIZED`, `PAYLOAD_TOO_LARGE`, `UNSUPPORTED_MEDIA_TYPE`, `BAD_REQUEST`,
  `CONFLICT` and a dynamic `HTTP_{status}` fallback. The previous review asked for this, so this is a
  gap between the code and the spec, not a coder error. Either way, clients will see codes the
  contract does not list.
  **Fix:** with the user's approval, add `METHOD_NOT_ALLOWED` (405) and `INTERNAL_ERROR` (500) to the
  spec's shared codes. Otherwise, trim `_HTTP_CODES` to the codes that framework errors can actually
  produce.

- [ ] **[Minor] A framework-raised 401 gets code `UNAUTHORIZED`, but the spec says `INVALID_TOKEN`** —
  `app/core/errors.py:94`. If the auth dependency (Task for `PrincipalDep`) uses FastAPI's
  `HTTPBearer(auto_error=True)`, a missing header raises `HTTPException(401)`. That error goes through
  `_http` and returns `UNAUTHORIZED`. The plan expects anonymous → 401 (`writing-plans-plan.md:1067`),
  and the spec allows only `INVALID_TOKEN` for 401.
  **Fix:** map `401: "INVALID_TOKEN"` in `_HTTP_CODES`, or require the auth dependency to raise
  `UnauthorizedError` itself (`auto_error=False`).

- [ ] **[Minor] Unhandled exceptions are logged twice** — `app/core/errors.py:145-148`. A handler
  registered for `Exception` runs inside Starlette's `ServerErrorMiddleware`. That middleware sends
  the 500 response and then re-raises (`starlette/middleware/errors.py:186`). Uvicorn then logs the
  same traceback again, outside structlog and its planned redaction processor. This is also why the
  test needs `raise_app_exceptions=False`. In addition, this 500 response is built outside every
  user middleware, so it will not get the correlation-id header that the Task with `main.py` adds.
  **Fix:** accept this and note it in the architecture spec. Or catch `Exception` in the outermost
  correlation-id middleware so the response carries the id and the error is logged once.

- [ ] **[Minor] No boundary test for `MAX_DEPTH`** — `tests/core/test_svg.py:62-67`. Only depth 5,000
  is tested. An off-by-one (`>` vs `>=`) or a later lowering of the limit would go unnoticed.
  **Fix:** parametrise the test with `MAX_DEPTH` (accepted) and `MAX_DEPTH + 1` (rejected), counting
  nesting levels from the root `<svg>`.

- [ ] **[Minor] The ORM model must repeat `nulls_not_distinct`** — `app/db/alembic/versions/0001_baseline.py:68-73`.
  When `FeatureFlag` is mapped (architecture: `db/models/admin.py`), `__table_args__` needs the same
  `UniqueConstraint(..., postgresql_nulls_not_distinct=True)`. Without it, autogenerate will report
  drift, and `on_conflict_do_update` will not target the right constraint.
  Also, line 122 of `errors.py` (a mapped `IntegrityError` going through the HTTP handler) and lines
  137-138 (a non-standard status) have no tests. Add a route that raises an `IntegrityError` whose
  `diag.constraint_name="uq_users_email"` and assert `409 EMAIL_TAKEN`.

### Carried over from the first review (not in scope for this fix, still open)
- Nothing committed yet. The diff in `writing-plans-plan.md` is still mixed in with the code.
- Dependency floors, naive cursor datetimes, `register_namespace` per call (`svg.py:73`), `ttl or`,
  rate limiter `aclose`, empty `target_metadata`, conftest hygiene, untested adapters, CI hardening.

### Positive Notes
- Each fix stays small and in its own file. `_internal_error()` builds the 500 envelope in one
  place, and the mapped and unmapped integrity paths share it.
- The `_http` handler keeps `exc.headers`, so 405 `Allow` (and a future 401 `WWW-Authenticate`) is
  not lost. The test asserts this.
- The depth check runs before the allowlist filter, so depth is bounded for the whole tree. Because
  it raises the domain error, uploads get a 422 and not a 500.
- `hash_salt` has no default, so a missing salt stops startup instead of silently using a public
  value. This is safer than a validator that rejects only `"change-me"`.
- The migration test uses `begin_nested()`, so the expected failure does not break the
  per-test SAVEPOINT fixture.

## Verification Evidence (DoD Standard tier, run in this review)

Run with the project venv binaries (`uv run` is still blocked by the pyenv shim), Python 3.12.13:

| Check | Command | Result |
|---|---|---|
| Lint | `.venv/bin/ruff check .` | All checks passed |
| Format | `.venv/bin/ruff format --check .` | 33 files already formatted |
| Types | `.venv/bin/mypy app tests` | Success: no issues found in 33 source files |
| Tests | `.venv/bin/pytest -q --cov=app` | **43 passed** (was 37), 3 third-party deprecation warnings |
| Coverage | same | 87% total; `errors.py` 96% (misses 122, 137-138), `svg.py` 98%, `config.py` 96% |
| File naming | `tasks/TASK-001/*` | all skill-prefixed |
| Uncommitted | `git status` | **FAIL**: Phase A is still untracked |

Library versions checked: SQLAlchemy 2.1.5, Starlette 1.8.0, FastAPI 0.144.0.

---

# Previous review — TASK-001 Phase A (Tasks 1-8), first pass

Scope: uv scaffold, `app/core/*`, `app/db/*` (base, session, Alembic env + `0001_baseline`),
`tests/` (conftest, fakes, core + db tests), `.github/workflows/ci.yml`, `pyproject.toml`.
Reviewed against `tasks/TASK-001/writing-plans-plan.md` Phase A, `specs/architect-architecture.md`,
`specs/api-designer-spec.md` Conventions, `.claude/DOD.md` (Standard), `.claude/GOLDEN-PRINCIPLES.md`.

## Summary

Phase A follows the plan closely, and in several places the implementation improves on the plan's own
snippets. Examples: `decode()` checks `exp` against the injected clock, the semaphore is per instance
instead of module-global, `Settings` uses `default_factory` and has no `# type: ignore`, the test
fixtures use NullPool, and the SVG tests add more hostile payloads. Layering is clean: `core/` has no
upward imports, and every third-party library (argon2, jwt, limits, boto3, zxcvbn, defusedxml) sits
behind `core/`.

No Critical issues. There are 4 Major issues: the error envelope has gaps, the `feature_flags`
uniqueness rule does not hold for NULL `trainer_id`, secrets in config are not protected, and deeply
nested SVG input crashes the sanitiser. There are 10 Minor issues. The work is also still
**uncommitted**, while the plan expects one commit per task.

## Issues Found

### Critical (Must Fix)
- None.

### Major (Should Fix)

- [ ] **[Major] Some errors do not use the error envelope** — `app/core/errors.py:89-109`.
  Spec Conventions: "Error envelope ... one handler in `core/errors.py`". Confirmed by running the code:
  `GET /missing` → `404 {"detail":"Not Found"}`, wrong method → `405 {"detail":"Method Not Allowed"}`,
  and an unmapped `IntegrityError` (`raise exc` on line 100) → `500` plain-text `Internal Server Error`.
  Clients will get two different error formats, and the Task 28 test `test_error_envelope_documented_for_4xx`
  will fail once real routes exist.
  **Fix:** in `install_error_handlers`, also register `starlette.exceptions.HTTPException` (map
  404→`NOT_FOUND`, 405→`METHOD_NOT_ALLOWED`, and so on, keeping `exc.headers`) and a catch-all
  `Exception` handler that logs the error and returns `500 {"error":{"code":"INTERNAL_ERROR",...}}`.
  The `_integrity` fallback should return that 500 envelope instead of re-raising.

- [ ] **[Major] `uq_feature_flags_key_trainer_id` does not stop duplicate global flags** —
  `app/db/alembic/versions/0001_baseline.py:68`. In Postgres, NULLs count as distinct in a UNIQUE
  constraint, so you can insert any number of `(key='x', trainer_id=NULL)` rows. A lookup of the global
  flag can then get more than one row and return an unpredictable result.
  **Fix (PG ≥ 15, we run 16):** `sa.UniqueConstraint("key", "trainer_id", name=..., postgresql_nulls_not_distinct=True)`,
  plus a migration test that a second global insert raises `IntegrityError`.

- [ ] **[Major] Secret settings have no protection or validation** — `app/core/config.py:16,25`.
  (a) `jwt_secret: str` accepts any length. A 1-char secret was accepted, so HS256 can run with a
  brute-forceable key. (b) `repr(Settings(...))` prints `jwt_secret='k'` in plain text, so any
  `log.info(settings=...)` or traceback leaks it. (c) `hash_salt = "change-me"` is a working default,
  so a production deploy that forgets `APP_HASH_SALT` silently hashes IP and user-agent values with a
  public salt (a privacy issue).
  **Fix:** `jwt_secret: SecretStr = Field(min_length=32)` and pass `.get_secret_value()` into
  `TokenIssuer`. Make `hash_salt: SecretStr` required (no default), or add a validator that rejects
  `"change-me"`. Add a test that a short secret raises `ValidationError`.

- [ ] **[Major] Deeply nested SVG crashes the sanitiser with `RecursionError` (returns 500)** — `app/core/svg.py:42-56`.
  Confirmed by running it: 5,000 nested `<g>` elements (about 40 KB, well under any upload limit) parse
  fine with expat, then `_copy` recurses until `RecursionError`. The client gets a 500 instead of
  `422 INVALID_LOGO`, and the error can be triggered on demand on an upload endpoint.
  **Fix:** add a depth guard to `_copy` (for example `MAX_DEPTH = 32`, raising `_invalid("Logo is too deeply nested")`),
  or rewrite the walk iteratively with an explicit stack. Add a parametrised test for it.

### Minor (Consider)

- [ ] **[Minor] Nothing is committed yet** — Phase A is all untracked files, and
  `writing-plans-plan.md` has an unrelated edit to "Open points". The plan's ground rule 1 and the
  DoD Minimum ("no uncommitted changes") expect one commit per task. **Fix:** commit before moving on
  to Phase B. Put the plan-doc edit in its own `docs(TASK-001)` commit.
- [ ] **[Minor] Dependency floors are lower than the architecture requires** — `pyproject.toml`:
  `pyjwt>=2.10`, `limits>=5.0` and `uuid-utils>=0.10`, but the architecture table says `pyjwt>=2.15` and
  `limits>=5.8`, and the plan says `uuid-utils>=1.0`. The lock file resolves to 2.15.1, 5.8.0 and 1.0.0,
  so this is safe today but could break if someone re-resolves. **Fix:** raise the floors to match the
  spec.
- [ ] **[Minor] `decode_cursor` accepts naive datetimes and has no length limit** — `app/core/pagination.py:27-32`, `:19`.
  A crafted cursor `["2026-01-01T00:00:00", ...]` decodes to a naive datetime. Comparing it with a
  `timestamptz` keyset in asyncpg can raise `DataError` and return a 500. `CursorQ` has no `max_length`.
  **Fix:** reject the cursor if `ts.tzinfo is None`, and use `Query(max_length=256)`.
- [ ] **[Minor] `ET.register_namespace` changes global state on every call** — `app/core/svg.py:70`.
  Move it to module import time.
- [ ] **[Minor] `ttl or self._ttl` ignores `ttl=0`** — `app/core/security.py:92`. Use
  `self._ttl if ttl is None else ttl` so an explicit 0 (or negative, in tests) is respected.
- [ ] **[Minor] The rate limiter leaks connections and has no Redis-outage policy** — `app/core/ratelimit.py:21-32`.
  There is no `aclose()`, so each test creates a new `RedisStorage` and never closes it. A Redis outage
  surfaces as a raw 500. **Fix:** add an `aclose()` and call it from the fixture and app lifespan.
  Decide on fail-open or fail-closed and record that in the architecture spec, which is not settled
  here.
- [ ] **[Minor] `target_metadata` is empty** — `app/db/alembic/env.py:9,12`. The baseline tables have
  no ORM models yet, so `alembic revision --autogenerate` would generate DROPs for
  `idempotency_keys`, `audit_log` and `feature_flags`. **Fix (Task 9):** add a `app/db/models/__init__.py`
  that imports every model, and import it in `env.py`. Note that `audit_log.metadata` collides with
  SQLAlchemy's reserved `metadata` attribute, so map it as `meta: Mapped[dict[str, object]] = mapped_column("metadata", JSONB)`.
- [ ] **[Minor] Test fixture hygiene** — `tests/conftest.py:34`: `Config("alembic.ini")` depends on the
  current directory, so use `Path(__file__).parents[1] / "alembic.ini"`. `tests/conftest.py:83`: the
  `Redis(...)` client is never closed, so use `with Redis(...) as r: r.flushall()`.
- [ ] **[Minor] Untested adapters** — `app/db/session.py` (0%), `app/core/email.py` (0%), and
  `app/core/storage.py` `S3ObjectStorage` (50%) have no tests. At minimum, add a `get_session`
  close test, a `LoggingEmailSender` test (it must not log `to` or the context, using `structlog.testing.capture_logs`),
  and an `S3ObjectStorage.head` 404→`None` test with `botocore.stub.Stubber`.
- [ ] **[Minor] CI hardening** — `.github/workflows/ci.yml`: add `permissions: contents: read`, and
  consider pinning actions by SHA and turning on `enable-cache: true` in `setup-uv`.

### Positive Notes

- `TokenIssuer.decode` pins `algorithms=["HS256"]`, requires `sub/role/exp/jti/typ`, rejects
  `alg=none` and non-`access` `typ`, and checks `exp` against the injected `Clock`. That makes the
  time-boundary tests deterministic.
- `PasswordHasher.verify(None, ...)` runs a dummy Argon2 verify, which prevents user enumeration by
  timing. Hashing runs in worker threads behind a semaphore.
- The SVG sanitiser uses an allowlist and rebuilds the tree from scratch (it does not strip a copy).
  It drops namespaced and `on*` attributes, `javascript:` and `data:` values, and whole subtrees of
  unknown elements, and `forbid_dtd=True` blocks XXE and entity bombs. The tests cover all of these.
- `map_integrity_error` maps errors by constraint name, as ground rule 5 requires, and the naming
  convention in `db/base.py` produces names that match the mapping table.
- The test infrastructure is solid: testcontainers, migrate-once, SAVEPOINT rollback per test, a
  separate truncating `committed_engine` for concurrency tests, Redis flushed per test, and fakes
  that satisfy the Protocols.
- `mypy --strict` passes with no `# type: ignore` in the codebase. The plan's own
  `# type: ignore[call-arg]` was removed correctly.

## Verification Evidence (DoD Standard tier, run in this review)

`uv run` fails on this machine (`pyenv: version '3.12' is not installed`, because the pyenv shim
captures `uv`), so the gate was run with the project venv binaries directly (Python 3.12.13):

| Check | Command | Result |
|---|---|---|
| Lint | `.venv/bin/ruff check .` | All checks passed |
| Format | `.venv/bin/ruff format --check .` | 33 files already formatted |
| Types | `.venv/bin/mypy app tests` | Success: no issues found in 33 source files |
| Tests | `.venv/bin/pytest -q` | **37 passed**, 3 warnings (testcontainers deprecation, third-party) |
| Coverage | `pytest --cov=app` | 86% total; gaps: `db/session.py` 0%, `core/email.py` 0%, `core/storage.py` 50% |
| TODO/FIXME | `grep -rn "TODO\|FIXME\|HACK" app tests` | none |
| File naming | `tasks/TASK-001/*` | all skill-prefixed |
| Uncommitted | `git status` | **FAIL**: all Phase A files untracked (see Minor #1) |

Probes run during the review: SVG with 5,000 nested elements → `RecursionError`; naive-datetime cursor
→ accepted; `Settings(jwt_secret="k")` → accepted and shown in `repr`; framework 404/405 and unmapped
`IntegrityError` → responses without the envelope.
