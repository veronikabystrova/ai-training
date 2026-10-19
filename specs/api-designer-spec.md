# API Specification

> Living spec. Read `MANIFEST.md` and `architect-architecture.md` first. New work is appended as `### [TASK-N] METHOD /path (date)` sections.
> Structure, `deps.py` contract and data model live in `architect-architecture.md`; this file does not repeat them, it references them.
> Base path: `/api/v1`. OpenAPI: `/api/v1/openapi.json`. Epic-01 SPEC §8 is the base; this file records the shapes and the TASK-001 deltas (design §API Design).

## Conventions (apply to every endpoint)

| Topic | Rule |
|---|---|
| Resources | Plural nouns; actions as `POST /{resource}/{id}/{verb}`. Documented exceptions (G-22): `PUT /me/avatar`, `PUT /me/availability`, `PUT /me/players/{id}/availability` (full replace = genuine PUT) |
| Success codes | GET 200 · POST create 201 · POST action 200 (or 202 when async/deferred) · PATCH 200 · PUT 200 · DELETE 204 |
| Error envelope (Conv §5) | `{"error": {"code": "STRING", "message": "...", "details": [...]}}`; one handler in `core/errors.py`. `details` items: `{"field": "a.b", "message": "..."}` (422) or code-specific objects |
| Validation | Every request model `ConfigDict(extra="forbid")` ⇒ unknown field 422 `VALIDATION_ERROR`. Query params are declared, never read from `request` |
| Tenant | Never in a body. trainer/coach: from `Principal`. player: `?trainer_id=` via `PlayerContextDep` (A-02). Cross-tenant ⇒ 404 `NOT_FOUND` |
| Auth | `Authorization: Bearer <access JWT>`. Public routes carry no dependency (or `OptionalPrincipalDep`) |
| IDs / time | UUIDv7 strings; instants RFC 3339 UTC (`Z`); availability times are wall-clock `HH:MM` in the profile timezone |
| Pagination | Cursor: `?limit=1..100 (default 50)&cursor=<opaque>` ⇒ `Page[T] = {items: [T], next_cursor: str | null}` |
| Idempotency | Header `Idempotency-Key` (≤ 64 chars) on `POST /me/approvals/{id}/approve|deny`; replay returns stored response; different body hash ⇒ 409 `IDEMPOTENCY_KEY_REUSED` |
| Rate limits | Defaults 300/min per principal (per client IP on routes without a principal, e.g. `/auth/refresh`). Overrides listed per endpoint. 429 `RATE_LIMITED` + `Retry-After` (seconds) |
| Response headers | `X-Correlation-Id` always. `X-Impersonated-By: <admin user_id>` when `act` is set. `Cache-Control: public, max-age=300` only on public branding |
| Privacy | No schema exposes `password_hash`, token hashes, IP/UA hashes, `legal_retention_until`. Raw one-time/refresh tokens appear only in the response that issues them or in the email job |
| Forced password change | Provisioned trainers are `pending` and cannot log in (401 `INVALID_CREDENTIALS`). `POST /auth/password/setup` activates the account and clears `must_change_password` in one transaction, so no authenticated principal ever carries `must_change_password=true` |
| Route outline | `api/v1/<domain>/routes.py` (thin: schema in, service call, schema out) + `schemas.py`. Routers: `auth`, `me` (incl. `/me/players`, `/me/approvals`), `trainer`, `public`, `admin` |

### Shared schemas

```python
class Role(StrEnum): super_admin = "super_admin"; trainer = "trainer"; coach = "coach"; player = "player"

class UserStatus(StrEnum): pending = "pending"; active = "active"; inactive = "inactive"; deleted = "deleted"

class ErrorDetail(BaseModel):
    field: str | None = None
    message: str

class ErrorBody(BaseModel):
    code: str
    message: str
    details: list[ErrorDetail | dict[str, Any]] = []

class ErrorResponse(BaseModel):
    error: ErrorBody

class Page[T](BaseModel):
    items: list[T]
    next_cursor: str | None = None

class TokenPair(BaseModel):
    access_token: str
    refresh_token: str            # opaque, 256-bit; only ever returned here
    token_type: Literal["bearer"] = "bearer"
    expires_in: int = 900         # access TTL seconds

class SessionUser(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    role: Role
    email: EmailStr
    email_verified: bool
    must_change_password: bool
    trainer_id: UUID | None       # trainer self / coach active tenant / None for player
    child: bool

class AuthResponse(BaseModel):
    tokens: TokenPair
    user: SessionUser

Password = Annotated[str, Field(min_length=12, max_length=128)]   # zxcvbn >= 3 enforced by PasswordPolicy => 422 WEAK_PASSWORD

class WeeklyWindow(BaseModel):
    weekday: int = Field(ge=0, le=6)        # 0 = Monday
    start_time: time                        # HH:MM, start < end else 422
    end_time: time

class AvailabilitySet(BaseModel):
    timezone: str                           # IANA, validated via zoneinfo
    windows: list[WeeklyWindow] = Field(max_length=70)

class CoachPublicFields(BaseModel):
    bio: str | None = Field(default=None, max_length=2000)
    credentials: str | None = Field(default=None, max_length=2000)
    certifications: list[Certification] = Field(default=[], max_length=50)
    is_public: bool = False

class Certification(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    issuer: str | None = Field(default=None, max_length=200)
    year: int | None = Field(default=None, ge=1950, le=2100)
```

Shared error codes (any endpoint): 401 `INVALID_TOKEN`, 401 `IMPERSONATION_EXPIRED` (every 401 carries `WWW-Authenticate: Bearer`), 403 `FORBIDDEN`,
404 `NOT_FOUND`, 405 `METHOD_NOT_ALLOWED` (keeps the `Allow` header), 422 `VALIDATION_ERROR`,
429 `RATE_LIMITED`, 500 `INTERNAL_ERROR`. Any other framework-raised status returns `HTTP_{status}`
(e.g. `HTTP_415`).

---

## Domain: Auth (`/auth`, tag `auth`)

### [TASK-001] POST /auth/register (2026-10-18)

**Domain:** Auth. **Access:** public or optional principal. **Rate limit:** 20/h per IP.

Query (exactly one of): `share_code: str` or `camp_submission_id: UUID`. Neither or both ⇒ 422.

```python
class RegisterRequest(BaseModel):                      # extra="forbid"
    # email/password/first_name/last_name are required only when a new account is created (missing ⇒ 422
    # VALIDATION_ERROR); an authenticated redeem may send just `player_profile_ids` (user decision 2026-10-18)
    email: EmailStr | None = Field(default=None, max_length=255)
    password: Password | None = None
    first_name: str | None = Field(default=None, min_length=1, max_length=100)
    last_name: str | None = Field(default=None, min_length=1, max_length=100)
    phone: str | None = Field(default=None, max_length=32)
    timezone: str = "UTC"
    birth_date: date | None = None                     # required for a new player account (static link), else 422 VALIDATION_ERROR;
                                                       # must not be in the future; ignored for coach links (user decision 2026-10-18)
    player_profile_ids: list[UUID] | None = None       # parent registering existing dependents for a static link
```

Behaviour (flows per design §Key flows): static player link ⇒ new player user + own player profile + `trainer_player_links(source=share_link)`;
`coach_unique` link ⇒ new coach user + `coach_profiles(is_active=true)` (unauthenticated, new email only; G-12).
Authenticated existing user ⇒ link only, body ignored except `player_profile_ids`. An already active link is a no-op:
`use_count` is not incremented and no `registered` event is written (user decision 2026-10-18).
`camp_submission_id` is a stub until Epic-08 defines how a submission maps to a trainer: it always returns 404 `NOT_FOUND`.

**Response:** `201 AuthResponse` (new account) · `200 {"user": SessionUser, "linked_trainer_id": UUID}` (authenticated existing user).

**Status Codes:**
- 201 / 200 as above
- 403 `LINK_EMAIL_MISMATCH` (coach link, email differs from `target_email`) · 403 `CHILD_CANNOT_REDEEM` · 403 `EMAIL_NOT_VERIFIED` (authenticated redeem by unverified user)
- 404 `SHARE_LINK_INVALID` (unknown / inactive / expired / exhausted: one code) · 404 `NOT_FOUND` (profile id not owned)
- 409 `ACCOUNT_EXISTS` (`details: [{"next": "login_then_redeem"}]`) · 409 `WRONG_LINK_KIND` · 409 `COACH_ALREADY_ASSIGNED` (`details: [{"current_trainer_id": UUID}]`)
- 422 `VALIDATION_ERROR` / `WEAK_PASSWORD` (empty `player_profile_ids` ⇒ 422)
- 429

**OpenAPI:** summary "Register via ShareLink or camp submission", `response_model=AuthResponse`, `status_code=201`, `responses={200,403,404,409,422,429}`.

### [TASK-001] POST /auth/login (2026-10-18)

**Access:** public. **Rate limit:** 10/min per IP and 5/min per email.

```python
class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)
```

**Response:** `200 AuthResponse`. Failure for unknown user, wrong password, pending, inactive and deleted users is always the same 401 (constant-time dummy hash; no account-state oracle).

```json
{
  "tokens": {"access_token": "eyJ...", "refresh_token": "Zm9v...", "token_type": "bearer", "expires_in": 900},
  "user": {"id": "0192f1a0-...", "role": "coach", "email": "sam@example.com", "email_verified": false,
           "must_change_password": false, "trainer_id": "0192f19e-...", "child": false}
}
```

**Status Codes:** 200 · 401 `INVALID_CREDENTIALS` · 422 · 429.

### [TASK-001] POST /auth/refresh (2026-10-18)

**Access:** public (the refresh token is the credential). **Rate limit:** default.

```python
class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=20, max_length=200)
```

**Response:** `200 TokenPair`. Rotation: old token consumed, successor in same family. Token claims are rebuilt from the DB (new coach tenant, deactivation honoured).
Impersonation families never outlive `impersonation_sessions.expires_at` (G-14).
Reuse detection is strict, with no grace window (user decision 2026-10-18): any second use of a consumed token revokes the family, including
two tabs or a retry after a lost response. **Client contract:** send at most one refresh per family at a time (single-flight)
and share the result across tabs.

**Status Codes:** 200 · 401 `INVALID_REFRESH_TOKEN` (unknown/expired/revoked) · 401 `REFRESH_REUSE_DETECTED` (family revoked, audit row) · 401 `IMPERSONATION_EXPIRED` · 422.

### [TASK-001] POST /auth/logout (2026-10-18)

**Access:** any authenticated principal (also accepted while `must_change_password`). Revokes the caller's refresh family.

```python
class LogoutRequest(BaseModel):
    refresh_token: str            # identifies the family; must belong to the caller
```

**Status Codes:** 204 · 401 · 404 `NOT_FOUND` (token not the caller's) · 422.

### [TASK-001] POST /auth/password-reset/request (2026-10-18)

**Access:** public. **Rate limit:** 5/h per email (counter keyed on the lowercased input) plus the default per client IP. A limited request returns 429 for any email, known or unknown, so the 429 itself reveals nothing.

```python
class PasswordResetRequest(BaseModel):
    email: EmailStr
```

**Response:** `202 {"status": "accepted"}` always. For an active user: issues a `password_reset` one-time token (1 h, supersedes open ones) and enqueues `send_password_reset`.

**Status Codes:** 202 · 422 · 429.

### [TASK-001] POST /auth/password-reset/confirm (2026-10-18)

**Access:** public.

```python
class PasswordResetConfirm(BaseModel):
    token: str = Field(min_length=20, max_length=200)
    new_password: Password
```

Consumes the token atomically (A-05); sets the password and revokes all refresh families in the same transaction.

**Status Codes:** 204 · 400 `TOKEN_INVALID` (unknown/expired/used) · 422 `WEAK_PASSWORD` (token is **not** consumed when validation fails: password policy runs before consume) · 429.

### [TASK-001] POST /auth/verify-email (2026-10-18)

**Access:** public. Body `{"token": str}` (`VerifyEmailRequest`). Consumes `email_verify`; sets `is_email_verified=true`.

**Status Codes:** 204 · 400 `TOKEN_INVALID` · 422.

### [TASK-001] POST /auth/verify-email/resend (2026-10-18)

**Access:** any authenticated principal. **Rate limit:** 3/h per user. No body. Supersedes open `email_verify` tokens, enqueues the email job.

**Status Codes:** 202 `{"status": "accepted"}` · 200 `{"status": "already_verified"}` · 401 · 429.

### [TASK-001] POST /auth/password/setup (2026-10-18)

**Access:** public (the `trainer_setup` token is the credential). Added by G-03.

```python
class PasswordSetupRequest(BaseModel):
    token: str = Field(min_length=20, max_length=200)
    new_password: Password
```

Consumes `trainer_setup` (24 h); `pending → active`, clears `must_change_password`, sets `is_email_verified=true` (invite reached the mailbox).

**Response:** `200 AuthResponse`. **Status Codes:** 200 · 400 `TOKEN_INVALID` · 422 `WEAK_PASSWORD` · 429.

The new password is validated **before** the token is consumed, so a `422 WEAK_PASSWORD` leaves the setup link usable for a retry. `password-reset/confirm` follows the same order.

### [TASK-001] POST /auth/impersonation/exit (2026-10-18)

**Access:** principal with `act` set. Ends `impersonation_sessions` (revokes `jti` and its refresh family).
**Response:** `204`. **Status Codes:** 204 · 401 `IMPERSONATION_EXPIRED` · 403 `FORBIDDEN` (no `act`).

---

## Domain: Me (`/me`, tag `me`)

All routes: any authenticated role unless stated. Player-role routes that are tenant-bound take `?trainer_id` through `PlayerContextDep`.

### [TASK-001] GET /me (2026-10-18)

p95 < 100 ms (NFR-002). **Response:** `200 MeResponse`.

```python
class MeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    role: Role
    email: EmailStr
    email_verified: bool
    must_change_password: bool
    first_name: str
    last_name: str
    phone: str | None
    school: str | None
    timezone: str
    avatar_url: str | None
    child: bool
    impersonated_by: UUID | None             # Principal.act
    coach: CoachPublicFields | None          # role == coach only
    trainer_id: UUID | None
```

**Status Codes:** 200 · 401 · 404 (trainer tenant deactivated, G-15).

### [TASK-001] PATCH /me (2026-10-18)

```python
class UpdateMe(BaseModel):                           # extra="forbid"; email, role, status are not editable
    first_name: str | None = Field(default=None, min_length=1, max_length=100)
    last_name: str | None = Field(default=None, min_length=1, max_length=100)
    phone: str | None = Field(default=None, max_length=32)
    school: str | None = Field(default=None, max_length=200)
    timezone: str | None = None
    # coach-only (any other role sending these ⇒ 422 FIELD_NOT_ALLOWED, FR-208)
    bio: str | None = Field(default=None, max_length=2000)
    credentials: str | None = Field(default=None, max_length=2000)
    certifications: list[Certification] | None = Field(default=None, max_length=50)
    is_public: bool | None = None
```

**Response:** `200 MeResponse`. **Status Codes:** 200 · 401 · 422 `VALIDATION_ERROR` / `FIELD_NOT_ALLOWED`.

### [TASK-001] PUT /me/avatar (2026-10-18)

Presign step (documented PUT exception, G-22).

```python
class AvatarPresignRequest(BaseModel):
    content_type: Literal["image/jpeg", "image/png", "image/webp"]
    content_length: int = Field(gt=0, le=5 * 1024 * 1024)

class PresignResponse(BaseModel):
    upload_url: HttpUrl          # presigned PUT, constrains type and length
    key: str                     # server-chosen: avatars/{uuid7}
    expires_in: int              # seconds
    required_headers: dict[str, str]
```

**Response:** `200 PresignResponse`. **Status Codes:** 200 · 422 (type/size outside allowlist).

### [TASK-001] POST /me/avatar/confirm (2026-10-18)

```python
class AvatarConfirmRequest(BaseModel):
    avatar_key: str = Field(pattern=r"^avatars/[0-9a-f-]{36}$")
```

HEAD check (≤ 5 MB, jpeg/png/webp) then persist. **Response:** `200 MeResponse`. **Status Codes:** 200 · 404 (key missing) · 422 `INVALID_AVATAR`.

### [TASK-001] POST /me/email-change (2026-10-18)

```python
class EmailChangeRequest(BaseModel):
    new_email: EmailStr = Field(max_length=255)
    password: str                 # re-auth
```

Issues `email_change` token (24 h) to the **new** address; old email stays active. **Response:** `202 {"status": "accepted"}`.
**Rate limit:** default (300/min per principal). Password guessing with a stolen access token is an **accepted risk** (user decision 2026-10-19); no dedicated limit.
**Status Codes:** 202 · 403 `INVALID_CURRENT_PASSWORD` · 409 `EMAIL_TAKEN` (checked again at confirm via `uq_users_email`) · 422 · 429.

### [TASK-001] POST /me/email-change/confirm (2026-10-18)

**Access:** public. Body `{"token": str}`. Atomic consume + swap + revoke refresh families in one transaction.
**Status Codes:** 204 · 400 `TOKEN_INVALID` · 409 `EMAIL_TAKEN` (consumption rolled back).

### [TASK-001] GET /me/availability, PUT /me/availability (2026-10-18)

**Access:** player (own profile) and coach. Not tenant-scoped (profile-owned). trainer/super_admin ⇒ 403.

`GET` ⇒ `200 AvailabilitySet`. `PUT` body `AvailabilitySet` is a full weekly replace (delete + insert in one transaction) ⇒ `200 AvailabilitySet`.

**Status Codes:** 200 · 403 · 422 `OVERLAPPING_WINDOWS` (GiST `[)` semantics: touching windows allowed) · 422 `VALIDATION_ERROR` (start ≥ end, bad tz).

### [TASK-001] GET /me/contexts (2026-10-18)

**Access:** player only (A-02). Lists valid `?trainer_id` values.

```python
class PlayerContext(BaseModel):
    trainer_id: UUID
    business_name: str
    logo_url: str | None
    player_profile_ids: list[UUID]            # profiles the caller may act for under this trainer
    linked_at: datetime
```

**Response:** `200 list[PlayerContext]` (no pagination; bounded by links). Deactivated trainers omitted. **Status Codes:** 200 · 403.

### Dependents: `/me/players` (tag `me-players`, role player, not `child`)

```python
class Gender(StrEnum): female = "female"; male = "male"; other = "other"; undisclosed = "undisclosed"

class ChildLogin(BaseModel):
    email: EmailStr
    password: Password

class CreatePlayer(BaseModel):
    display_name: str = Field(min_length=1, max_length=100)
    birth_date: date                          # age 1-17 (1 <= age < 18) on Clock.today(), else 422 INVALID_AGE
    gender: Gender
    skill_level: str | None = Field(default=None, max_length=50)
    trainer_ids: list[UUID] = Field(default=[], max_length=20)   # must already be linked to the parent
    token_spend_requires_approval: bool = False
    login: ChildLogin | None = None

class UpdatePlayer(BaseModel):                # all optional; `login` absent = unchanged, null = revoke, object = create/replace
    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    birth_date: date | None = None
    gender: Gender | None = None
    skill_level: str | None = None
    token_spend_requires_approval: bool | None = None
    login: ChildLogin | None = None           # distinguished with model_fields_set

class TrainerLinkResponse(BaseModel):
    trainer_id: UUID
    business_name: str
    status: Literal["active", "inactive"]
    source: Literal["share_link", "parent_added", "camp_submission"]
    linked_at: datetime

class PlayerResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    display_name: str
    birth_date: date
    gender: Gender
    skill_level: str | None
    is_dependent: bool
    token_spend_requires_approval: bool
    has_login: bool                           # login_user_id set and that user active
    trainers: list[TrainerLinkResponse]
```

### [TASK-001] GET /me/players (2026-10-18)

**Response:** `200 list[PlayerResponse]` (caller's own profile first, then dependents). **Status Codes:** 200 · 403.

### [TASK-001] POST /me/players (2026-10-18)

Query: `allow_duplicate: bool = False`. **Request:** `CreatePlayer`. Child login (if any) created in the same transaction: `users(role=player, active, pre-verified)`.
**Accepted risk (user decision 2026-10-19):** the email is not verified, so a mistyped address lets that mailbox's owner take the child
account over via password reset.

**Response:** `201 PlayerResponse`.

**Status Codes:** 201 · 403 `CHILD_CANNOT_CREATE_DEPENDENT` · 404 (a `trainer_ids` entry not linked) · 409 `DUPLICATE_PLAYER` (same display_name + birth_date) · 409 `EMAIL_TAKEN` (child login) · 422 `INVALID_AGE` / `WEAK_PASSWORD` / `VALIDATION_ERROR`.

### [TASK-001] GET /me/players/{id} (2026-10-18)

**Response:** `200 PlayerResponse`. Not owner ⇒ 404. Child may read only its own profile.

### [TASK-001] PATCH /me/players/{id} (2026-10-18)

**Request:** `UpdatePlayer`. `login: null` sets the login user `inactive` (refresh families revoked; `login_user_id` retained).
**Status Codes:** 200 · 403 (child) · 404 · 409 `EMAIL_TAKEN` · 422.

### [TASK-001] DELETE /me/players/{id} (2026-10-18)

Soft-removes a dependent (profile inactive, links inactive, child login inactive); history kept.
**Status Codes:** 204 · 404 · 409 `ACTIVE_COMMITMENTS` (`details: [{"rsvp_ids": [...]}]`, via `CommitmentChecker`). The parent's own profile cannot be removed ⇒ 409 `CANNOT_REMOVE_OWN_PROFILE`.

### [TASK-001] PUT /me/players/{id}/availability (2026-10-18)

Dependent's weekly set; same body/response/errors as `PUT /me/availability`. Another owner ⇒ 404. Child principal ⇒ 403.
A dependent's times are always in the **parent's (owner's) profile timezone** (user decision 2026-10-19): a `timezone` that differs from the
parent's ⇒ 422 `VALIDATION_ERROR`; this route never changes the parent's profile. A child login reading its own availability
sees the owner's timezone.

### [TASK-001] POST /me/players/{id}/trainers (2026-10-18)

```python
class AddTrainerLink(BaseModel):
    trainer_id: UUID              # must be a trainer the parent is already linked to (parent_added source)
```

**Response:** `201 TrainerLinkResponse` (new or reactivated) · `200 TrainerLinkResponse` (already active).
**Status Codes:** 200 · 201 · 403 (child) · 404 (not owner / trainer unknown, inactive or not linked to the parent).

### [TASK-001] DELETE /me/players/{id}/trainers/{trainer_id} (2026-10-18)

Soft `inactive`. **Status Codes:** 204 · 404 · 409 `ACTIVE_COMMITMENTS`.

### Approvals: `/me/approvals` (tag `me-approvals`, role player parent; reads are tenant-bound: `?trainer_id` required)

```python
class ApprovalStatus(StrEnum): pending="pending"; approved="approved"; denied="denied"; expired="expired"

class ApprovalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    trainer_id: UUID
    player_profile_id: UUID
    kind: Literal["purchase"]
    amount: Decimal
    currency: Literal["USD", "TOKENS"]
    description: str
    status: ApprovalStatus
    created_at: datetime
    expires_at: datetime
    decided_at: datetime | None
    decision_note: str | None

class DenyRequest(BaseModel):
    note: str | None = Field(default=None, max_length=500)
```

### [TASK-001] GET /me/approvals (2026-10-18)

Query: `trainer_id: UUID` (required), `status: ApprovalStatus | None`, `limit`, `cursor`. **Response:** `200 Page[ApprovalResponse]` (only the caller's dependents').
**Status Codes:** 200 · 403 · 404 (trainer not a valid context).

### [TASK-001] POST /me/approvals/{id}/approve (2026-10-18)

Headers: `Idempotency-Key` (required). No body. Calls `PurchaseExecutor.execute` in the same transaction; row-locked decision (10-way parallel ⇒ one effect).
Approve re-checks that the dependent profile and its trainer link are still active; otherwise 404 `NOT_FOUND` and no purchase
(user decision 2026-10-19). Deny is still allowed so the parent can clear the request.
**Response:** `200 ApprovalResponse`. **Status Codes:** 200 · 400 `IDEMPOTENCY_KEY_REQUIRED` · 404 (another parent's; dependent removed or trainer link inactive) · 409 `APPROVAL_NOT_PENDING` · 409 `IDEMPOTENCY_KEY_REUSED`.

### [TASK-001] POST /me/approvals/{id}/deny (2026-10-18)

Headers: `Idempotency-Key`. Body `DenyRequest`. Enqueues child-notification job. **Response:** `200 ApprovalResponse`. **Status Codes:** as approve.

---

## Domain: Trainer (`/trainer`, tag `trainer`; roles trainer + coach unless stated; tenant from `TenantDep`)

```python
class ShareLinkKind(StrEnum): player_static = "player_static"; coach_unique = "coach_unique"

class CreateShareLink(BaseModel):
    kind: ShareLinkKind
    target_email: EmailStr | None = None      # required iff kind == coach_unique, forbidden otherwise (422)

class ShareLinkResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    code: str
    kind: ShareLinkKind
    url: str
    target_email: EmailStr | None
    expires_at: datetime | None               # coach_unique: now + 7 d; static: null
    max_uses: int | None                      # coach_unique: 1; static: null (unlimited)
    use_count: int
    is_active: bool
    created_at: datetime

class AvailabilityRead(AvailabilitySet):
    subject_id: UUID                          # player_profile_id or coach user_id
    subject_type: Literal["player", "coach"]

class BrandingResponse(BaseModel):
    business_name: str
    logo_url: str | None
    primary_color: str | None                 # #RRGGBB
    secondary_color: str | None

class UpdateBranding(BaseModel):
    business_name: str | None = Field(default=None, min_length=1, max_length=120)
    primary_color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    secondary_color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
```

### [TASK-001] POST /trainer/share-links (2026-10-18)

**Access:** trainer only for `coach_unique` (coach ⇒ 403); trainer and coach for `player_static`. `trainer_id` from principal.
**Response:** `201 ShareLinkResponse`.
```json
{"id": "0192f1b3-...", "code": "k3Jx9...", "kind": "coach_unique", "url": "https://app.example.com/join/k3Jx9...",
 "target_email": "sam@example.com", "expires_at": "2026-10-25T10:00:00Z", "max_uses": 1, "use_count": 0, "is_active": true,
 "created_at": "2026-10-18T10:00:00Z"}
```
**Status Codes:** 201 · 403 · 409 `COACH_ALREADY_ASSIGNED` (target email is an active coach elsewhere; names the trainer) · 422.

### [TASK-001] GET /trainer/share-links (2026-10-18)

Query: `kind`, `is_active`, `limit`, `cursor`. **Response:** `200 Page[ShareLinkResponse]` (own tenant only).

### [TASK-001] DELETE /trainer/share-links/{id} (2026-10-18)

Deactivates (soft). **Status Codes:** 204 · 404 (other tenant's) · idempotent on an already-inactive link.

### [TASK-001] GET /trainer/players/{id}/availability (2026-10-18)

`id` = `player_profile_id`; requires an active `trainer_player_links` row to this tenant. **Response:** `200 AvailabilityRead`. **Status Codes:** 200 · 404.

### [TASK-001] GET /trainer/coaches/{id}/availability (2026-10-18)

`id` = coach `user_id` (architecture 3.1). Only a coach with an **active** tenure at this trainer; trainer role only. **Response:** `200 AvailabilityRead`. **Status Codes:** 200 · 403 (coach role) · 404 (other tenant's, former coach).

### [TASK-001] GET /trainer/branding (2026-10-18)

**Access:** trainer, coach (read). **Response:** `200 BrandingResponse`.

### [TASK-001] PATCH /trainer/branding (2026-10-18)

**Access:** trainer only. Body `UpdateBranding`. **Response:** `200 BrandingResponse`. **Status Codes:** 200 · 403 · 422 (colour regex).

### [TASK-001] POST /trainer/branding/logo (2026-10-18)

Presign: body `{content_type: Literal["image/png","image/svg+xml","image/webp"], content_length: int <= 2 MB}`. **Response:** `200 PresignResponse` (key `logos/{uuid7}`). **Access:** trainer. **Status Codes:** 200 · 403 · 422.

### [TASK-001] POST /trainer/branding/logo/confirm (2026-10-18)

Body `{logo_key: str pattern ^logos/[0-9a-f-]{36}$}`. HEAD re-check; SVG passes `sanitize_svg` and is re-serialised before persist. **Response:** `200 BrandingResponse`.
**Status Codes:** 200 · 403 · 404 · 422 `INVALID_LOGO`.

---

## Domain: Public (`/public`, tag `public`; no auth)

### [TASK-001] GET /public/share-links/{code} (2026-10-18)

**Rate limit:** 60/min per IP. Records `share_link_events(opened)` (salted SHA-256 IP/UA).

```python
class ShareLinkPreview(BaseModel):
    trainer_name: str
    business_name: str
    logo_url: str | None
    kind: ShareLinkKind
    valid: Literal[True] = True              # invalid links are 404, never `valid: false`
    target_email_hint: str | None            # coach_unique only: "s***@example.com"
```

**Status Codes:** 200 · 404 `SHARE_LINK_INVALID` (also deactivated trainer, G-15) · 429.

### [TASK-001] GET /public/trainers/{slug}/branding (2026-10-18)

Exposes only logo URL, colours and business name. Header `Cache-Control: public, max-age=300`. **Response:** `200 BrandingResponse`.
**Status Codes:** 200 · 404 (unknown slug or deactivated trainer).

---

## Domain: Admin (`/admin`, tag `admin`; every route requires `SuperAdminScope`; impersonating admins ⇒ 403)

```python
class AdminUserSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    email: EmailStr
    role: Role
    status: UserStatus
    first_name: str
    last_name: str
    trainer_id: UUID | None
    created_at: datetime
    last_login_at: datetime | None

class AdminUserDetail(AdminUserSummary):
    phone: str | None
    school: str | None
    timezone: str
    email_verified: bool
    must_change_password: bool
    deleted_at: datetime | None
    business_name: str | None                       # trainers only (US-01.01 response); null otherwise
    slug: str | None                                # trainers only (US-01.01 response); null otherwise

class ProvisionTrainer(BaseModel):
    email: EmailStr = Field(max_length=255)
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    phone: str | None = Field(default=None, max_length=32)   # present in the US-01.01 example body
    business_name: str = Field(min_length=1, max_length=120)
    slug: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", min_length=3, max_length=60)
    timezone: str = "UTC"

class AdminUpdateUser(BaseModel):                   # extra="forbid"; role and email are immutable here
    first_name: str | None = Field(default=None, min_length=1, max_length=100)
    last_name: str | None = Field(default=None, min_length=1, max_length=100)
    phone: str | None = Field(default=None, max_length=32)
    timezone: str | None = None

class ImpersonationResponse(BaseModel):
    access_token: str                               # sub=target, act=admin, exp <= 60 min
    refresh_token: str
    expires_at: datetime
    target: SessionUser
```

### [TASK-001] GET /admin/users (2026-10-18)

Query: `role`, `status`, `trainer_id`, `query` (pg_trgm over email and full name, min 2 chars), `limit`, `cursor`. Keyset on `(created_at, id)`. p95 < 500 ms at 10 k users (index scan).
**Response:** `200 Page[AdminUserSummary]`. **Status Codes:** 200 · 403 · 422.

### [TASK-001] POST /admin/users/trainers (2026-10-18)

Users + profiles + trainer_profiles in one transaction; random 32-byte password, `status=pending`, `must_change_password=true`; `send_trainer_invite` with a 24 h `trainer_setup` token. Audit `trainer.created`.
**Response:** `201 AdminUserDetail` (no password, no token).
**Status Codes:** 201 · 403 · 409 `EMAIL_TAKEN` / `SLUG_TAKEN` (from constraint names) · 422.

### [TASK-001] GET /admin/users/{id} (2026-10-18)

**Response:** `200 AdminUserDetail`. **Status Codes:** 200 · 403 · 404.

### [TASK-001] PATCH /admin/users/{id} (2026-10-18)

Body `AdminUpdateUser`. **Response:** `200 AdminUserDetail`. Audit `user.updated`. **Status Codes:** 200 · 403 · 404 · 409 `USER_ERASED` · 422.

### [TASK-001] POST /admin/users/{id}/deactivate (2026-10-18)

`active → inactive`; revokes all refresh families; next access-token use fails (A-03). Deactivated trainer hides its tenant (G-15). Audit.
**Response:** `200 AdminUserDetail`. **Status Codes:** 200 (idempotent when already inactive) · 403 · 404 · 409 `USER_ERASED`.

### [TASK-001] POST /admin/users/{id}/reactivate (2026-10-18)

`inactive → active`. **Response:** `200 AdminUserDetail`. **Status Codes:** 200 · 403 · 404 · 409 `USER_ERASED` · 409 `INVALID_STATUS_TRANSITION` (`pending` users are activated only by `password/setup`).

### [TASK-001] DELETE /admin/users/{id} (2026-10-18)

GDPR erasure: anonymise in place, `status=deleted`, revoke families, delete the user's `one_time_tokens`, erase dependents in the same transaction, write `user_erasures` (hashed email).
`user_erasures.legal_retention_until = erased_at + settings.erasure_retention_years` (default 7; pending legal sign-off).
**Response:** `204`. **Status Codes:** 204 · 403 · 404 · 409 `ALREADY_ERASED`.

### [TASK-001] POST /admin/users/{id}/impersonate (2026-10-18)

No body. Creates `impersonation_sessions` (≤ 60 min); token `sub=target, act=admin`. Target must be `active`.
**Response:** `200 ImpersonationResponse`. **Status Codes:** 200 · 403 `CANNOT_IMPERSONATE_ADMIN` · 404 · 409 `USER_NOT_ACTIVE`.

---

## Permission summary (drives the generated 403/404 test matrix)

| Domain | super_admin | trainer | coach | player | child | anonymous |
|---|---|---|---|---|---|---|
| `/auth/*` | per endpoint (public unless noted) | | | | | |
| `/me`, `/me/avatar*`, `/me/email-change*` | yes | yes | yes | yes | yes | no |
| `/me/availability` | 403 | 403 | yes | yes (own) | yes (own) | no |
| `/me/contexts`, `/me/approvals*` | 403 | 403 | 403 | yes | 403 | no |
| `/me/players*` | 403 | 403 | 403 | yes | read own only | no |
| `/trainer/share-links` (`coach_unique`) | 403 | yes | 403 | 403 | 403 | no |
| `/trainer/*` other | 403 | yes | read only | 403 | 403 | no |
| `/public/*` | yes | yes | yes | yes | yes | yes |
| `/admin/*` | yes (not impersonating) | 403 | 403 | 403 | 403 | 401 |

## Error code registry (new in TASK-001, on top of Epic-01 §9)

| Code | Status | Raised by |
|---|---|---|
| `INVALID_CREDENTIALS` | 401 | login |
| `INVALID_REFRESH_TOKEN`, `REFRESH_REUSE_DETECTED` | 401 | refresh |
| `TOKEN_INVALID` | 400 | any one-time-token consume |
| `WEAK_PASSWORD` | 422 | register, reset, setup, child login |
| `INVALID_CURRENT_PASSWORD` | 403 | email change |
| `FIELD_NOT_ALLOWED` | 422 | `PATCH /me` coach-only fields from a non-coach |
| `INVALID_AVATAR`, `INVALID_LOGO` | 422 | confirm endpoints |
| `IDEMPOTENCY_KEY_REQUIRED` | 400 | approvals |
| `IDEMPOTENCY_KEY_REUSED` | 409 | approvals |
| `COACH_ALREADY_ASSIGNED` | 409 | register (coach link), share-link create |
| `CANNOT_REMOVE_OWN_PROFILE`, `INVALID_STATUS_TRANSITION`, `USER_NOT_ACTIVE` | 409 | see endpoints |

## Resolved decisions (TASK-001, 2026-10-18, confirmed by user)

1. **Schemas vs spec examples:** schemas are derived from the FR text, but Epic-01 SPEC §9 JSON examples take precedence. Applied: `ProvisionTrainer.phone` (optional) added; `AdminUserDetail` gains `business_name` and `slug` so the US-01.01 `201` returns `{id, email, business_name, slug, status}`.
2. **`?trainer_id=`:** `GET /me/approvals` requires it (spec §10: no cross-trainer aggregated view). `/me/availability` and `/me/players*` are profile-owned and not tenant-bound.
3. **`DELETE /me/players/{id}`** is a soft removal (Conv §5; erasure stays a super_admin action per the §6 matrix). **`POST /auth/password/change` is dropped:** it is not in the requirements. Forgotten or changed passwords use password reset; provisioned trainers use `password/setup`.
4. **Self-guard codes dropped** (`CANNOT_DEACTIVATE_SELF`, `CANNOT_ERASE_SELF`): stick to the spec.
5. **Password policy is validated before the one-time token is consumed** on `password-reset/confirm` and `password/setup`.
6. **Dependent age:** `1 <= age < 18` (spec §10), so US-01.03's "1–18" is treated as 1–17.
7. **`legal_retention_until`:** `erased_at + settings.erasure_retention_years`, default 7. Pending legal sign-off.
