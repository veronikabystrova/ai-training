# Phase 0 Foundation + Epic-01 Identity & Tenancy API Implementation Plan

**Task:** TASK-001

> **For Claude:** Use `git-worktrees` to create isolated workspace, then implement with `coder` skill.

**Goal:** Build the FastAPI service foundation and every Epic-01 REST endpoint (auth, me, players, approvals, trainer, public, admin) exactly as specified in `specs/api-designer-spec.md`.

**Architecture:** Horizontal layers `api/v1/<domain>/routes.py -> services -> repositories -> models` (A-01). Identity and tenant come only from `api/deps.py` (`Principal`, `TenantScope`, `player_context`, `SuperAdminScope`). Services own transactions; third-party libs sit behind `core/` wrappers; cross-epic needs are `typing.Protocol` seams with fakes (A-06). The DB is authoritative on every request (A-03).

**Tech Stack:** Python 3.12, uv, ruff, mypy strict, pytest + pytest-asyncio + testcontainers (Postgres 16, Redis 7), FastAPI, Pydantic v2 + pydantic-settings, SQLAlchemy 2 async + asyncpg, Alembic, ARQ, structlog, argon2-cffi, pyjwt, zxcvbn, defusedxml, limits, uuid-utils, boto3.

---

## Read First (zero-context engineer)

Do not duplicate these documents; open them when a task says "Spec:".

| File | What it holds |
|---|---|
| `specs/api-designer-spec.md` | Every endpoint: schemas, status codes, error codes, rate limits, permission matrix. **Source of truth for shapes.** Copy request/response models from it verbatim into `schemas.py` |
| `specs/architect-architecture.md` | Module map, `deps.py` contract, data model, library decisions, A-01..A-07 |
| `tasks/TASK-001/brainstorm-design.md` | Key flows (login, coach_unique redemption), status machine, testing strategy |
| `tasks/TASK-001/requirements-analyst-requirements.md` | Models table, repositories, services, jobs, test inventory, gap ids (G-xx) |
| `AGENTS.md`, `.claude/DOD.md` | Policy: run tests and lint before claiming done, never `--no-verify`, never read `.env` |

## Ground rules for every task

1. TDD: failing test, see it fail for the stated reason, minimal code, see it pass, commit.
2. Layer rules: routes call services only; services call repositories and `core/`; repositories never `commit()`; domain code never calls `datetime.now()` (inject `Clock`); services never import `argon2`, `jwt`, `limits`, `boto3`, `zxcvbn`, `defusedxml`.
3. Tenant id never comes from a request body. Tenant-scoped repository methods take `trainer_id` as a required keyword-only argument. Cross-tenant access returns 404.
4. Every request model has `model_config = ConfigDict(extra="forbid")`.
5. `IntegrityError` is mapped to domain errors by constraint name in `core/errors.py` only. No pre-check SELECTs for uniqueness.
6. After every task run the quality gate (QG):
   `uv run ruff check . && uv run ruff format --check . && uv run mypy app tests && uv run pytest -q`
7. Commit messages: conventional commits. Never `--no-verify`.
8. New routes are added to the permission matrix (Task 26) in the same commit as the route.

Package root is `app/` at repository root (per architecture §1). Tests live in `tests/` mirroring `app/`.

---

## Phase A: Foundation

### Task 1: Project scaffold

**Files:**
- Create: `pyproject.toml`, `.python-version`, `.gitignore` (append `.env`, `.venv`, `__pycache__`), `docker-compose.yml`, `app/__init__.py`, `tests/__init__.py`, `tests/test_smoke.py`, `.github/workflows/ci.yml`

**Step 1: Write the failing test**

```python
# tests/test_smoke.py
def test_package_importable() -> None:
    import app

    assert app.__name__ == "app"
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_smoke.py`
Expected: FAIL (`No pyproject.toml` / `ModuleNotFoundError: app`)

**Step 3: Write minimal implementation**

```bash
uv init --package --python 3.12 --name trainer-platform .
uv add fastapi "pydantic[email]" pydantic-settings "sqlalchemy[asyncio]" asyncpg alembic redis arq boto3 \
  python-multipart structlog "argon2-cffi>=25.1" "pyjwt>=2.15" "zxcvbn>=4.5" "defusedxml>=0.7.1" \
  "limits>=5.8" "uuid-utils>=1.0" "uvicorn[standard]"
uv add --dev pytest pytest-asyncio pytest-cov httpx testcontainers[postgres,redis] ruff mypy pip-audit \
  types-boto3 freezegun
```

`pyproject.toml` tool sections (append):

```toml
[tool.ruff]
line-length = 100
target-version = "py312"
[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "S", "ASYNC", "RUF"]
[tool.ruff.lint.per-file-ignores]
"tests/**" = ["S101", "S105", "S106"]
[tool.mypy]
strict = true
plugins = ["pydantic.mypy"]
[tool.pytest.ini_options]
asyncio_mode = "auto"
markers = ["gate: merge-gate tests run on every CI run", "concurrency: uses committed data and parallel connections"]
```

`docker-compose.yml`: services `postgres:16` (db `app`, port 5432) and `redis:7` (port 6379). CI workflow: checkout, `astral-sh/setup-uv`, run QG, `uv run pip-audit`.

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_smoke.py`
Expected: PASS

**Step 5: Commit**

```bash
git add pyproject.toml uv.lock .python-version .gitignore docker-compose.yml app tests .github
git commit -m "chore: scaffold uv project with ruff, mypy strict, pytest"
```

---

### Task 2: `core/ids`, `core/clock`, `core/config`

**Files:**
- Create: `app/core/__init__.py`, `app/core/ids.py`, `app/core/clock.py`, `app/core/config.py`
- Test: `tests/core/test_ids_clock_config.py`, `tests/fakes.py`

**Step 1: Write the failing test**

```python
# tests/core/test_ids_clock_config.py
from datetime import UTC, datetime
from uuid import UUID

from app.core.clock import SystemClock
from app.core.config import Settings
from app.core.ids import new_id
from tests.fakes import FakeClock


def test_new_id_is_uuid7_and_time_ordered() -> None:
    a, b = new_id(), new_id()
    assert isinstance(a, UUID) and a.version == 7
    assert a < b


def test_fake_clock_advances() -> None:
    clock = FakeClock(datetime(2026, 10, 18, tzinfo=UTC))
    clock.advance(minutes=15)
    assert clock.now() == datetime(2026, 10, 18, 0, 15, tzinfo=UTC)
    assert clock.today().isoformat() == "2026-10-18"


def test_system_clock_is_tz_aware() -> None:
    assert SystemClock().now().tzinfo is not None


def test_settings_defaults() -> None:
    s = Settings(jwt_secret="x" * 32, database_url="postgresql+asyncpg://u:p@h/db", redis_url="redis://h")
    assert s.access_ttl_seconds == 900
    assert s.refresh_ttl_days == 30
    assert s.impersonation_max_minutes == 60
    assert s.erasure_retention_years == 7
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/core/test_ids_clock_config.py`
Expected: FAIL with ImportError

**Step 3: Write minimal implementation**

```python
# app/core/ids.py
from uuid import UUID

from uuid_utils.compat import uuid7


def new_id() -> UUID:
    return uuid7()
```

```python
# app/core/clock.py
from datetime import UTC, date, datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...
    def today(self) -> date: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)

    def today(self) -> date:
        return self.now().date()
```

```python
# tests/fakes.py  (grown in later tasks)
from datetime import date, datetime, timedelta


class FakeClock:
    def __init__(self, start: datetime) -> None:
        self._now = start

    def now(self) -> datetime:
        return self._now

    def today(self) -> date:
        return self._now.date()

    def advance(self, **kwargs: float) -> None:
        self._now += timedelta(**kwargs)
```

```python
# app/core/config.py
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="APP_", env_file=None, extra="ignore")

    database_url: str
    redis_url: str
    jwt_secret: str
    access_ttl_seconds: int = 900
    refresh_ttl_days: int = 30
    impersonation_max_minutes: int = 60
    ott_ttl_hours: dict[str, int] = {"trainer_setup": 24, "password_reset": 1, "email_verify": 24, "email_change": 24}
    coach_link_ttl_days: int = 7
    approval_ttl_hours: int = 48
    idempotency_ttl_hours: int = 24
    erasure_retention_years: int = 7
    hash_salt: str = "change-me"           # salt for ip/user-agent hashes
    argon2_time_cost: int = 3
    argon2_memory_kib: int = 65536
    argon2_parallelism: int = 4
    public_base_url: str = "https://app.example.com"
    s3_bucket: str = "app-media"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
```

**Step 4:** Run: `uv run pytest tests/core/test_ids_clock_config.py` Expected: PASS

**Step 5: Commit**

```bash
git add app/core tests
git commit -m "feat(core): add ids, clock and settings"
```

---

### Task 3: Error contract (`core/errors.py`)

**Files:**
- Create: `app/core/errors.py`
- Test: `tests/core/test_errors.py`

**Step 1: Write the failing test**

```python
# tests/core/test_errors.py
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError

from app.core.errors import ConflictError, NotFoundError, install_error_handlers, map_integrity_error


def make_app() -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)

    class Body(BaseModel):
        n: int

    @app.get("/nf")
    async def nf() -> None:
        raise NotFoundError()

    @app.get("/conflict")
    async def conflict() -> None:
        raise ConflictError("COACH_ALREADY_ASSIGNED", "taken", details=[{"current_trainer_id": "abc"}])

    @app.post("/v")
    async def v(b: Body) -> None: ...

    return app


async def test_error_envelope_shape() -> None:
    async with AsyncClient(transport=ASGITransport(app=make_app()), base_url="http://t") as c:
        r = await c.get("/nf")
        assert r.status_code == 404
        assert r.json() == {"error": {"code": "NOT_FOUND", "message": "Not found", "details": []}}
        r = await c.get("/conflict")
        assert r.status_code == 409 and r.json()["error"]["details"] == [{"current_trainer_id": "abc"}]


async def test_validation_error_uses_envelope() -> None:
    async with AsyncClient(transport=ASGITransport(app=make_app()), base_url="http://t") as c:
        r = await c.post("/v", json={"n": "x"})
        body = r.json()["error"]
        assert r.status_code == 422 and body["code"] == "VALIDATION_ERROR"
        assert body["details"][0]["field"] == "n"


@pytest.mark.parametrize(
    ("constraint", "code"),
    [("uq_users_email", "EMAIL_TAKEN"), ("uq_trainer_profiles_slug", "SLUG_TAKEN"),
     ("uq_coach_one_active_trainer", "COACH_ALREADY_ASSIGNED"), ("ex_availability_no_overlap", "OVERLAPPING_WINDOWS")],
)
def test_integrity_error_mapped_by_constraint_name(constraint: str, code: str) -> None:
    class Diag:
        constraint_name = constraint

    class Orig(Exception):
        diag = Diag()

    err = map_integrity_error(IntegrityError("stmt", {}, Orig()))
    assert err is not None and err.code == code
```

**Step 2:** Run: `uv run pytest tests/core/test_errors.py` Expected: FAIL ImportError

**Step 3: Write minimal implementation**

```python
# app/core/errors.py
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError


class DomainError(Exception):
    status = 400
    code = "BAD_REQUEST"
    message = "Bad request"

    def __init__(self, code: str | None = None, message: str | None = None,
                 details: list[dict[str, Any]] | None = None, headers: dict[str, str] | None = None) -> None:
        self.code = code or self.code
        self.message = message or self.message
        self.details = details or []
        self.headers = headers or {}


class BadRequestError(DomainError): status = 400
class UnauthorizedError(DomainError): status, code, message = 401, "INVALID_TOKEN", "Invalid token"
class ForbiddenError(DomainError): status, code, message = 403, "FORBIDDEN", "Forbidden"
class NotFoundError(DomainError): status, code, message = 404, "NOT_FOUND", "Not found"
class ConflictError(DomainError): status, code, message = 409, "CONFLICT", "Conflict"
class UnprocessableError(DomainError): status, code, message = 422, "VALIDATION_ERROR", "Validation failed"
class RateLimitedError(DomainError): status, code, message = 429, "RATE_LIMITED", "Too many requests"

_CONSTRAINTS: dict[str, tuple[type[DomainError], str]] = {
    "uq_users_email": (ConflictError, "EMAIL_TAKEN"),
    "uq_trainer_profiles_slug": (ConflictError, "SLUG_TAKEN"),
    "uq_coach_one_active_trainer": (ConflictError, "COACH_ALREADY_ASSIGNED"),
    "ex_availability_no_overlap": (UnprocessableError, "OVERLAPPING_WINDOWS"),
}


def constraint_name(exc: IntegrityError) -> str | None:
    # asyncpg (our driver): name is on orig.__cause__.constraint_name; psycopg: orig.diag.constraint_name.
    orig = exc.orig
    name = getattr(getattr(orig, "diag", None), "constraint_name", None)
    if name is None:
        name = getattr(getattr(orig, "__cause__", None), "constraint_name", None)
    return name if isinstance(name, str) else None


def map_integrity_error(exc: IntegrityError) -> DomainError | None:
    hit = _CONSTRAINTS.get(constraint_name(exc) or "")
    return hit[0](hit[1], hit[1].replace("_", " ").capitalize()) if hit else None


def _envelope(code: str, message: str, details: list[Any]) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details}}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _domain(_: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(_envelope(exc.code, exc.message, exc.details), exc.status, headers=exc.headers)

    @app.exception_handler(IntegrityError)
    async def _integrity(_: Request, exc: IntegrityError) -> JSONResponse:
        mapped = map_integrity_error(exc)
        if mapped is None:
            raise exc
        return JSONResponse(_envelope(mapped.code, mapped.message, []), mapped.status)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = [{"field": ".".join(str(p) for p in e["loc"][1:]), "message": e["msg"]} for e in exc.errors()]
        return JSONResponse(_envelope("VALIDATION_ERROR", "Validation failed", details), 422)
```

**Step 4:** Run: `uv run pytest tests/core/test_errors.py` Expected: PASS

**Step 5: Commit**

```bash
git add app/core/errors.py tests/core/test_errors.py
git commit -m "feat(core): add error envelope and constraint-name mapping"
```

---

### Task 4: Pagination (`core/pagination.py`)

**Files:** Create `app/core/pagination.py`; Test `tests/core/test_pagination.py`

**Step 1: Write the failing test**

```python
from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.core.errors import UnprocessableError
from app.core.pagination import Page, decode_cursor, encode_cursor

def test_cursor_roundtrip() -> None:
    ts, ident = datetime(2026, 10, 18, tzinfo=UTC), UUID(int=7)
    assert decode_cursor(encode_cursor(ts, ident)) == (ts, ident)

def test_garbage_cursor_is_422() -> None:
    with pytest.raises(UnprocessableError):
        decode_cursor("not-a-cursor")

def test_page_envelope() -> None:
    assert Page[int](items=[1]).model_dump() == {"items": [1], "next_cursor": None}
```

**Step 2:** Run: `uv run pytest tests/core/test_pagination.py` Expected: FAIL ImportError

**Step 3: Implementation**

```python
# app/core/pagination.py
import base64
import json
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import Query
from pydantic import BaseModel

from app.core.errors import UnprocessableError


class Page[T](BaseModel):
    items: list[T]
    next_cursor: str | None = None


LimitQ = Annotated[int, Query(ge=1, le=100)]
CursorQ = Annotated[str | None, Query()]


def encode_cursor(ts: datetime, ident: UUID) -> str:
    raw = json.dumps([ts.isoformat(), str(ident)]).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        ts, ident = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        return datetime.fromisoformat(ts), UUID(ident)
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise UnprocessableError("VALIDATION_ERROR", "Invalid cursor") from exc
```

**Step 4:** Run the test. Expected: PASS. **Step 5:** `git add app/core/pagination.py tests/core/test_pagination.py && git commit -m "feat(core): add cursor pagination"`

---

### Task 5: Security primitives (`core/security.py`)

Spec: architecture §4, §5. Hashing runs in a thread behind a semaphore (architecture §6).

**Files:** Create `app/core/security.py`; Test `tests/core/test_security.py`

**Step 1: Write the failing test**

```python
import jwt
import pytest
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.core.errors import UnauthorizedError, UnprocessableError
from app.core.security import PasswordHasher, PasswordPolicy, TokenIssuer, hash_token, new_opaque_token
from tests.fakes import FakeClock

CLOCK = FakeClock(datetime(2026, 10, 18, tzinfo=UTC))

async def test_hash_verify_and_dummy_verify() -> None:
    h = PasswordHasher(time_cost=1, memory_kib=8, parallelism=1)
    digest = await h.hash("correct horse battery staple")
    assert await h.verify(digest, "correct horse battery staple") is True
    assert await h.verify(digest, "nope") is False
    assert await h.verify(None, "anything") is False       # unknown user: runs dummy verify, returns False

def test_policy_rejects_short_and_weak() -> None:
    p = PasswordPolicy()
    with pytest.raises(UnprocessableError) as e:
        p.validate("short1!")
    assert e.value.code == "WEAK_PASSWORD"
    with pytest.raises(UnprocessableError):
        p.validate("password1234")                           # zxcvbn score < 3
    p.validate("tangerine-Quasar-ledger-77")

def test_hash_token_is_sha256_32_bytes_and_raw_is_urlsafe() -> None:
    raw = new_opaque_token()
    assert len(hash_token(raw)) == 32 and len(raw) >= 43

def test_access_token_roundtrip_and_expiry() -> None:
    ti = TokenIssuer(secret="s" * 32, access_ttl=900, clock=CLOCK)
    uid = uuid4()
    tok, jti = ti.issue_access(user_id=uid, role="trainer", trainer_id=uid, act=None, child=False)
    claims = ti.decode(tok)
    assert claims["sub"] == str(uid) and claims["typ"] == "access" and claims["jti"] == str(jti)
    CLOCK.advance(minutes=15, seconds=1)
    with pytest.raises(UnauthorizedError):
        ti.decode(tok)

def test_decode_rejects_alg_none_and_wrong_typ() -> None:
    ti = TokenIssuer(secret="s" * 32, access_ttl=900, clock=CLOCK)
    forged = jwt.encode({"sub": "x"}, key="", algorithm="none")
    with pytest.raises(UnauthorizedError):
        ti.decode(forged)
```

**Step 2:** Run: `uv run pytest tests/core/test_security.py` Expected: FAIL ImportError

**Step 3: Implementation**

```python
# app/core/security.py
import asyncio
import hashlib
import os
import secrets
from typing import Any, Protocol
from uuid import UUID

import anyio.to_thread
import jwt
from argon2 import PasswordHasher as _Argon2
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from zxcvbn import zxcvbn

from app.core.clock import Clock
from app.core.errors import UnauthorizedError, UnprocessableError
from app.core.ids import new_id

_SEM = asyncio.Semaphore(os.cpu_count() or 2)


def hash_token(raw: str) -> bytes:
    return hashlib.sha256(raw.encode()).digest()


def new_opaque_token() -> str:
    return secrets.token_urlsafe(32)


class PasswordHasher:
    def __init__(self, *, time_cost: int = 3, memory_kib: int = 65536, parallelism: int = 4) -> None:
        self._h = _Argon2(time_cost=time_cost, memory_cost=memory_kib, parallelism=parallelism)
        self._dummy = self._h.hash("dummy-password-for-constant-time")

    async def hash(self, password: str) -> str:
        async with _SEM:
            return await anyio.to_thread.run_sync(self._h.hash, password)

    async def verify(self, digest: str | None, password: str) -> bool:
        target = digest or self._dummy
        async with _SEM:
            try:
                ok = await anyio.to_thread.run_sync(self._h.verify, target, password)
            except (VerifyMismatchError, VerificationError, InvalidHashError):
                return False
        return ok and digest is not None

    def needs_rehash(self, digest: str) -> bool:
        return self._h.check_needs_rehash(digest)


class PasswordPolicyPort(Protocol):
    def validate(self, password: str, user_inputs: list[str] | None = None) -> None: ...


class PasswordPolicy:
    def validate(self, password: str, user_inputs: list[str] | None = None) -> None:
        if len(password) < 12 or zxcvbn(password, user_inputs=user_inputs or [])["score"] < 3:
            raise UnprocessableError("WEAK_PASSWORD", "Password is too weak")


class TokenIssuer:
    def __init__(self, *, secret: str, access_ttl: int, clock: Clock) -> None:
        self._secret, self._ttl, self._clock = secret, access_ttl, clock

    def issue_access(self, *, user_id: UUID, role: str, trainer_id: UUID | None, act: UUID | None,
                     child: bool, ttl: int | None = None, jti: UUID | None = None) -> tuple[str, UUID]:
        jti = jti or new_id()
        now = int(self._clock.now().timestamp())
        claims: dict[str, Any] = {"sub": str(user_id), "role": role, "tid": str(trainer_id) if trainer_id else None,
                                  "act": str(act) if act else None, "child": child, "jti": str(jti),
                                  "typ": "access", "iat": now, "exp": now + (ttl or self._ttl)}
        return jwt.encode(claims, self._secret, algorithm="HS256"), jti

    def decode(self, token: str) -> dict[str, Any]:
        try:
            claims = jwt.decode(token, self._secret, algorithms=["HS256"], leeway=0,
                                options={"require": ["sub", "role", "exp", "jti", "typ"]},
                                # pyjwt compares exp to wall clock; inject clock via `now` for tests
                                )
        except jwt.PyJWTError as exc:
            raise UnauthorizedError("INVALID_TOKEN", "Invalid token") from exc
        if claims["typ"] != "access":
            raise UnauthorizedError("INVALID_TOKEN", "Invalid token")
        return claims
```

Note for the implementer: `pyjwt` checks `exp` against real time, so `test_access_token_roundtrip_and_expiry` needs `decode` to verify `exp` itself using `self._clock`. Decode with `options={"verify_exp": False, ...}` and compare `claims["exp"] <= int(self._clock.now().timestamp())` manually, raising `UnauthorizedError`. Make that change in this step before running the test.

**Step 4:** Run: `uv run pytest tests/core/test_security.py` Expected: PASS

**Step 5:** `git add app/core/security.py tests/core/test_security.py && git commit -m "feat(core): add argon2id hasher, zxcvbn policy, JWT issuer, token hashing"`

---

### Task 6: DB base, session, Alembic, test DB fixtures

**Files:**
- Create: `app/db/__init__.py`, `app/db/base.py`, `app/db/session.py`, `alembic.ini`, `app/db/alembic/env.py`, `app/db/alembic/versions/0001_baseline.py`, `tests/conftest.py`
- Test: `tests/db/test_baseline_migration.py`

**Step 1: Write the failing test**

```python
# tests/db/test_baseline_migration.py
from sqlalchemy import text

async def test_baseline_extensions_and_range_type(db_session) -> None:
    ext = {r[0] for r in await db_session.execute(text("select extname from pg_extension"))}
    assert {"citext", "btree_gist", "pg_trgm"} <= ext
    r = await db_session.execute(text("select 1 from pg_type where typname = 'timerange'"))
    assert r.scalar() == 1

async def test_baseline_tables_exist(db_session) -> None:
    names = {r[0] for r in await db_session.execute(text("select tablename from pg_tables where schemaname='public'"))}
    assert {"idempotency_keys", "audit_log", "feature_flags"} <= names
```

**Step 2:** Run: `uv run pytest tests/db` Expected: FAIL `fixture 'db_session' not found`

**Step 3: Implementation**

`tests/conftest.py` fixtures:
- session-scoped `pg_container` (`PostgresContainer("postgres:16")`) and `redis_container` (`RedisContainer("redis:7")`).
- session-scoped `engine`: run `alembic upgrade head` once (programmatic `alembic.command.upgrade` with `sqlalchemy.url` set).
- function-scoped `db_session`: open connection, `begin()`, bind `AsyncSession(join_transaction_mode="create_savepoint")`, rollback at teardown.
- `committed_engine` fixture for `@pytest.mark.concurrency` tests (separate connections, truncate tables after).
- `clock` (FakeClock at 2026-10-18T10:00Z), `email` (FakeEmailSender, Task 9), `storage` (FakeObjectStorage, Task 9), `redis` flush per test.

```python
# app/db/base.py
from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.ids import new_id

NAMING = {"ix": "ix_%(table_name)s_%(column_0_N_name)s", "uq": "uq_%(table_name)s_%(column_0_N_name)s",
          "ck": "ck_%(table_name)s_%(constraint_name)s", "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
          "pk": "pk_%(table_name)s"}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


class IdMixin:
    id: Mapped[UUID] = mapped_column(primary_key=True, default=new_id)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
```

`app/db/session.py`: `create_engine(url)`, `async_sessionmaker(expire_on_commit=False)`, `get_session` dependency yielding one `AsyncSession` per request.

Baseline migration `0001_baseline.py` runs `CREATE EXTENSION IF NOT EXISTS citext; btree_gist; pg_trgm;`, `CREATE TYPE timerange AS RANGE (subtype = time)`, and creates `idempotency_keys` (`id, key varchar(64), principal_id uuid, endpoint, request_hash, response_status, response_body jsonb, created_at, expires_at`, `uq_idempotency_keys_key_principal_id`), `audit_log` and `feature_flags` per `brainstorm-design.md` Data Model. `downgrade()` drops all of them.

**Step 4:** Run: `uv run pytest tests/db` Expected: PASS (needs Docker)

**Step 5:** `git add app/db alembic.ini tests && git commit -m "feat(db): add base, session, baseline migration and test fixtures"`

---

### Task 7: Rate limiter and idempotency helpers

Spec: Conventions (Rate limits, Idempotency), brainstorm Key flows.

**Files:** Create `app/core/ratelimit.py`, `app/core/idempotency.py`; Test `tests/core/test_ratelimit.py`, `tests/core/test_idempotency.py`

**Step 1: Write the failing test**

```python
# tests/core/test_ratelimit.py
import pytest
from app.core.errors import RateLimitedError
from app.core.ratelimit import RateLimiter, RouteClass

async def test_login_ip_limit_10_per_minute(redis_url) -> None:
    rl = RateLimiter(redis_url)
    for _ in range(10):
        await rl.hit(RouteClass.login_ip, "1.2.3.4")
    with pytest.raises(RateLimitedError) as e:
        await rl.hit(RouteClass.login_ip, "1.2.3.4")
    assert int(e.value.headers["Retry-After"]) >= 1

async def test_keys_are_independent(redis_url) -> None:
    rl = RateLimiter(redis_url)
    for _ in range(10):
        await rl.hit(RouteClass.login_ip, "a")
    await rl.hit(RouteClass.login_ip, "b")          # no raise
```

```python
# tests/core/test_idempotency.py
from app.core.idempotency import request_hash

def test_request_hash_stable_across_key_order() -> None:
    assert request_hash("POST", "/x", {"a": 1, "b": 2}) == request_hash("POST", "/x", {"b": 2, "a": 1})
    assert request_hash("POST", "/x", {"a": 1}) != request_hash("POST", "/x", {"a": 2})
```

**Step 2:** Run: `uv run pytest tests/core/test_ratelimit.py tests/core/test_idempotency.py` Expected: FAIL ImportError

**Step 3: Implementation**

```python
# app/core/ratelimit.py
from enum import Enum

from limits import parse
from limits.aio.storage import RedisStorage
from limits.aio.strategies import FixedWindowRateLimiter

from app.core.errors import RateLimitedError


class RouteClass(Enum):
    login_ip = "10/minute"
    login_email = "5/minute"
    register_ip = "20/hour"
    password_reset_email = "5/hour"
    verify_resend_user = "3/hour"
    public_preview_ip = "60/minute"
    default_principal = "300/minute"


class RateLimiter:
    def __init__(self, redis_url: str) -> None:
        self._strategy = FixedWindowRateLimiter(RedisStorage(redis_url.replace("redis://", "async+redis://")))

    async def hit(self, route: RouteClass, key: str) -> None:
        item = parse(route.value)
        if not await self._strategy.hit(item, route.name, key.lower()):
            stats = await self._strategy.get_window_stats(item, route.name, key.lower())
            raise RateLimitedError(headers={"Retry-After": str(max(1, int(stats.reset_time - __import__("time").time())))})
```

(Replace the inline `__import__("time")` with a normal `import time` at the top.)

```python
# app/core/idempotency.py
import hashlib
import json


def request_hash(method: str, path: str, body: object) -> str:
    canon = json.dumps([method, path, body], sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canon.encode()).hexdigest()
```

**Step 4:** Run the tests. Expected: PASS. **Step 5:** `git add app/core tests/core && git commit -m "feat(core): add rate limiter and idempotency hashing"`

---

### Task 8: Email / storage ports, fakes, SVG sanitiser

Spec: architecture §4 (SVG), §5 (Uploads). Hostile payload list in brainstorm Testing Strategy.

**Files:** Create `app/core/email.py`, `app/core/storage.py`, `app/core/svg.py`; extend `tests/fakes.py`; Test `tests/core/test_svg.py`

**Step 1: Write the failing test**

```python
# tests/core/test_svg.py
import pytest
from app.core.errors import UnprocessableError
from app.core.svg import sanitize_svg

OK = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect width="10" height="10" fill="#fff"/></svg>'

def test_clean_svg_roundtrips() -> None:
    assert b"<rect" in sanitize_svg(OK)

@pytest.mark.parametrize("payload", [
    b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg"><foreignObject><div/></foreignObject></svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg" onload="x()"><rect/></svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg"><a href="javascript:alert(1)"><rect/></a></svg>',
])
def test_hostile_elements_and_attrs_are_stripped(payload: bytes) -> None:
    out = sanitize_svg(payload).lower()
    for bad in (b"<script", b"foreignobject", b"onload", b"javascript:"):
        assert bad not in out

def test_xxe_and_entity_bomb_rejected() -> None:
    xxe = b'<?xml version="1.0"?><!DOCTYPE s [<!ENTITY x SYSTEM "file:///etc/passwd">]><svg xmlns="http://www.w3.org/2000/svg">&x;</svg>'
    bomb = b'<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;&a;">]><svg xmlns="http://www.w3.org/2000/svg">&b;</svg>'
    for p in (xxe, bomb):
        with pytest.raises(UnprocessableError):
            sanitize_svg(p)

def test_non_svg_root_rejected() -> None:
    with pytest.raises(UnprocessableError) as e:
        sanitize_svg(b"<html/>")
    assert e.value.code == "INVALID_LOGO"
```

**Step 2:** Run: `uv run pytest tests/core/test_svg.py` Expected: FAIL ImportError

**Step 3: Implementation**

`core/svg.py`: parse with `defusedxml.ElementTree.fromstring(data, forbid_dtd=True)` catching `defusedxml.DefusedXmlException` and `ParseError` to raise `UnprocessableError("INVALID_LOGO", ...)`. Root local-name must be `svg`. Rebuild a new tree by walking the parsed tree and copying only allowlisted elements (`svg g path rect circle ellipse line polyline polygon defs linearGradient radialGradient stop clipPath title desc`) and allowlisted attributes (geometry, `fill stroke stroke-width opacity transform viewBox xmlns d points x y cx cy r rx ry width height id offset stop-color`), dropping every `on*` attribute and any attribute value containing `javascript:` or `data:`. Serialise the new tree with `xml.etree.ElementTree.tostring`.

`core/email.py`: `class EmailSender(Protocol): async def send(self, *, to: str, template: str, context: dict[str, str]) -> None`. SMTP/SES adapter is out of scope: ship `LoggingEmailSender` (logs template name only, never the token) and `FakeEmailSender` in `tests/fakes.py` capturing `sent: list[SentEmail]`.

`core/storage.py`: `class ObjectStorage(Protocol)` with `presign_put(key, content_type, content_length, expires_in) -> PresignedPut`, `head(key) -> ObjectMeta | None` (`content_type`, `size`), `get(key) -> bytes`, `put(key, data, content_type)`. Boto3 adapter `S3ObjectStorage` (blocking calls wrapped in `anyio.to_thread.run_sync`). `FakeObjectStorage` in `tests/fakes.py` backed by a dict.

**Step 4:** Run: `uv run pytest tests/core` Expected: PASS. **Step 5:** `git add app/core tests && git commit -m "feat(core): add email/storage ports with fakes and SVG sanitiser"`

---

## Phase B: Data layer

### Task 9: Identity models + migration `0002_identity`

Spec: requirements "Models" table and architecture §3.

**Carry-over from Phase A review (do these in this task):**
1. **`target_metadata`:** point `app/db/alembic/env.py` at `Base.metadata` with all models imported, so autogenerate does not propose dropping the baseline tables. This needs ORM models for the `0001_baseline` tables too: create `app/models/platform.py` with `IdempotencyKey`, `AuditLog` (map the `metadata` column to attribute `metadata_`; `metadata` is reserved on declarative models) and `FeatureFlag`, mirroring `0001_baseline` exactly. Test: `alembic check` (or `compare_metadata`) reports no diff after `upgrade head`.
2. **`nulls_not_distinct`:** the `FeatureFlag` model's `UniqueConstraint("key", "trainer_id")` needs `postgresql_nulls_not_distinct=True` to match `0001_baseline` (otherwise autogenerate diffs it away).
3. **Rate limiter fail-open** (architecture `rate_limit` row): in `RateLimiter.hit`, catch Redis connection/timeout errors (`redis.exceptions.ConnectionError`, `TimeoutError`), log `rate_limit_unavailable` (warning, with route class, no key/PII) and return without raising. Test with a `RateLimiter` pointed at an unreachable Redis URL: `hit` returns, no exception, warning logged once. `RateLimitedError` on a real limit is unchanged.

**Files:**
- Create: `app/models/__init__.py`, `app/models/identity.py`, `app/db/alembic/versions/0002_identity.py`
- Create (carry-over): `app/models/platform.py`; Modify (carry-over): `app/db/alembic/env.py`, `app/core/ratelimit.py`; Test: `tests/core/test_ratelimit.py`, `tests/db/test_metadata_matches_migrations.py`
- Test: `tests/db/test_identity_constraints.py`, `tests/factories.py`

**Step 1: Write the failing tests** (one per DB-level rule; use `make_user`, `make_trainer`, `make_coach`, `make_player` factories in `tests/factories.py`, each returning committed-in-session ORM rows)

```python
import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy import text

from app.core.errors import constraint_name  # asyncpg: no orig.diag, see Task 2

async def test_email_unique_case_insensitive(db_session, make_user) -> None:
    await make_user(email="A@x.com")
    with pytest.raises(IntegrityError) as e:
        await make_user(email="a@X.com")
    assert constraint_name(e.value) == "uq_users_email"

async def test_one_active_coach_tenure(db_session, make_trainer, make_coach) -> None:
    t1, t2 = await make_trainer(), await make_trainer()
    coach = await make_coach(t1)
    with pytest.raises(IntegrityError) as e:
        await make_coach(t2, user=coach.user)
    assert constraint_name(e.value) == "uq_coach_one_active_trainer"

async def test_inactive_tenures_do_not_collide(db_session, make_trainer, make_coach) -> None:
    t1, t2 = await make_trainer(), await make_trainer()
    c = await make_coach(t1)
    c.is_active, c.left_at = False, db_session.info["now"]
    await db_session.flush()
    await make_coach(t2, user=c.user)         # ok

async def test_availability_overlap_rejected_touching_allowed(db_session, make_player, add_window) -> None:
    p = await make_player()
    await add_window(p.id, weekday=0, start="09:00", end="10:00")
    await add_window(p.id, weekday=0, start="10:00", end="11:00")      # touching ok
    with pytest.raises(IntegrityError) as e:
        await add_window(p.id, weekday=0, start="10:30", end="11:30")
    assert constraint_name(e.value) == "ex_availability_no_overlap"

async def test_share_link_kind_checks(db_session, make_trainer, add_share_link) -> None:
    t = await make_trainer()
    with pytest.raises(IntegrityError):
        await add_share_link(t, kind="coach_unique", target_email=None)

async def test_fk_restrict(db_session, make_trainer) -> None:
    t = await make_trainer()
    with pytest.raises(IntegrityError):
        await db_session.execute(text("delete from users where id = :i"), {"i": t.id})

async def test_hex_colour_check(db_session, make_trainer) -> None:
    t = await make_trainer()
    with pytest.raises(IntegrityError):
        await db_session.execute(text("update trainer_profiles set brand_primary_color='red' where user_id=:i"), {"i": t.id})
```

**Step 2:** Run: `uv run pytest tests/db/test_identity_constraints.py` Expected: FAIL ImportError/ fixtures missing

**Step 3: Implementation**

Implement every model in the requirements "Models" table as SQLAlchemy 2 `Mapped[...]` classes in `app/models/identity.py`: `User`, `Profile`, `TrainerProfile`, `CoachProfile`, `PlayerProfile`, `TrainerPlayerLink`, `ShareLink`, `ShareLinkEvent`, `AvailabilityWindow`, `PurchaseApproval`, `ImpersonationSession`, `UserErasure`, `RefreshToken`, `OneTimeToken`. Mandatory details:

- `users.email` is `CITEXT`, constraint `uq_users_email`. Native enums: `user_role`, `user_status`, `ott_purpose`, `share_link_kind`, `link_source`, `link_status`, `approval_status`.
- `CoachProfile`: columns and constraints exactly as architecture §3.1 (`ck_coach_left_iff_inactive`, `uq_coach_one_active_trainer` partial unique `WHERE is_active`, `ix_coach_trainer_active`).
- `OneTimeToken`: architecture §3.2 (`token_hash bytea`, `uq_ott_token_hash`, partial index on open tokens).
- `AvailabilityWindow`: raw DDL in migration: `ALTER TABLE availability_windows ADD CONSTRAINT ex_availability_no_overlap EXCLUDE USING gist (subject_type WITH =, subject_id WITH =, weekday WITH =, timerange(start_time, end_time, '[)') WITH &&)` plus `ck_availability_start_before_end`.
- `ShareLink` CHECKs: `coach_unique` requires `target_email`, `max_uses = 1`, `expires_at`; `player_static` forbids `target_email`.
- `TrainerProfile`: `uq_trainer_profiles_slug`, `ck_trainer_profiles_brand_hex` (`~ '^#[0-9A-Fa-f]{6}$'` on both colours when not null).
- `pg_trgm` GIN indexes: `users.email gin_trgm_ops`, `profiles ((first_name || ' ' || last_name)) gin_trgm_ops`; keyset index `ix_users_created_at_id (created_at, id)`.
- All FKs `ondelete="RESTRICT"`. `PlayerProfile.owner_user_id NOT NULL`, no age CHECK.

`tests/factories.py` exposes pytest fixtures `make_user`, `make_trainer`, `make_coach`, `make_player`, `make_dependent`, `add_window`, `add_share_link`, and `principal_token(user, act=None)` (uses `TokenIssuer`). Hash passwords with a cheap `PasswordHasher(time_cost=1, memory_kib=8, parallelism=1)`.

**Step 4:** Run: `uv run pytest tests/db` plus `uv run alembic downgrade base && uv run alembic upgrade head` against the compose DB. Expected: PASS, downgrade clean.

**Step 5:** `git add app/models app/db tests && git commit -m "feat(models): add identity schema, constraints and factories"`

---

### Task 10: Repositories

Spec: requirements "Repositories" table; architecture §2 repository contract.

**Files:**
- Create: `app/repositories/{user,profile,coach_profile,player,trainer_player_link,share_link,availability,approval,impersonation,refresh_token,one_time_token,erasure,audit,idempotency}.py`
- Test: `tests/repositories/test_*.py`, `tests/repositories/test_tenant_signatures.py`

**Step 1: Write the failing tests.** Highest-value ones (write all of them before implementing):

```python
# tests/repositories/test_one_time_token.py
async def test_consume_is_single_use(db_session, make_user, clock) -> None:
    repo = OneTimeTokenRepository(db_session)
    u = await make_user()
    raw = await repo.issue(user_id=u.id, purpose="password_reset", ttl=timedelta(hours=1), now=clock.now())
    first = await repo.consume(raw=raw, purpose="password_reset", now=clock.now())
    assert first is not None and first.user_id == u.id
    assert await repo.consume(raw=raw, purpose="password_reset", now=clock.now()) is None

async def test_wrong_purpose_and_expired_do_not_consume(db_session, make_user, clock) -> None:
    repo = OneTimeTokenRepository(db_session)
    u = await make_user()
    raw = await repo.issue(user_id=u.id, purpose="email_verify", ttl=timedelta(hours=24), now=clock.now())
    assert await repo.consume(raw=raw, purpose="password_reset", now=clock.now()) is None
    clock.advance(hours=24, seconds=1)
    assert await repo.consume(raw=raw, purpose="email_verify", now=clock.now()) is None

async def test_issue_supersedes_open_tokens(db_session, make_user, clock) -> None:
    repo = OneTimeTokenRepository(db_session)
    u = await make_user()
    old = await repo.issue(user_id=u.id, purpose="password_reset", ttl=timedelta(hours=1), now=clock.now())
    await repo.issue(user_id=u.id, purpose="password_reset", ttl=timedelta(hours=1), now=clock.now())
    assert await repo.consume(raw=old, purpose="password_reset", now=clock.now()) is None
```

```python
# tests/repositories/test_tenant_signatures.py
import inspect
from app.repositories.trainer_player_link import TrainerPlayerLinkRepository
from app.repositories.share_link import ShareLinkRepository
from app.repositories.approval import PurchaseApprovalRepository

def test_tenant_scoped_methods_require_keyword_only_trainer_id() -> None:
    for cls in (TrainerPlayerLinkRepository, ShareLinkRepository, PurchaseApprovalRepository):
        for name, fn in inspect.getmembers(cls, inspect.isfunction):
            if name.startswith("_") or name in {"lock_by_code", "lock_by_id_for_parent"}:
                continue
            p = inspect.signature(fn).parameters
            assert "trainer_id" in p and p["trainer_id"].kind is inspect.Parameter.KEYWORD_ONLY, f"{cls.__name__}.{name}"
```

Also write: `test_refresh_token.py` (rotate marks `consumed_at`, `revoke_family` revokes all, `lock_by_hash` uses `FOR UPDATE`), `test_coach_profile.py` (`get_active(trainer_id=, user_id=)` returns `None` after the tenure closes), `test_availability.py` (replace is delete+insert in one flush, overlap raises mapped constraint), `test_share_link.py` (`increment_use(id)` returns False when `use_count >= max_uses`), `test_user.py` (`get_by_email` case-insensitive; directory query keyset order, `query` filter min 2 chars; `anonymise`), `test_approval.py` (cross-parent lookup returns None), `test_erasure_scan.py` placeholder is added in Task 24.

**Step 2:** Run: `uv run pytest tests/repositories` Expected: FAIL ImportError

**Step 3: Implementation.** Core of the OTT repository (the rest follow the same shape: async methods on a session, no commits, `trainer_id` kw-only where tenant-scoped):

```python
# app/repositories/one_time_token.py
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import new_id
from app.core.security import hash_token, new_opaque_token
from app.models.identity import OneTimeToken


class OneTimeTokenRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def issue(self, *, user_id: UUID, purpose: str, ttl: timedelta, now: datetime,
                    payload: dict | None = None) -> str:
        await self._s.execute(update(OneTimeToken)
                              .where(OneTimeToken.user_id == user_id, OneTimeToken.purpose == purpose,
                                     OneTimeToken.used_at.is_(None)).values(used_at=now))
        raw = new_opaque_token()
        self._s.add(OneTimeToken(id=new_id(), user_id=user_id, purpose=purpose, token_hash=hash_token(raw),
                                 payload=payload, expires_at=now + ttl))
        await self._s.flush()
        return raw

    async def consume(self, *, raw: str, purpose: str, now: datetime):
        row = (await self._s.execute(
            text("UPDATE one_time_tokens SET used_at = :now WHERE token_hash = :h AND purpose = :p "
                 "AND used_at IS NULL AND expires_at > :now RETURNING user_id, payload"),
            {"now": now, "h": hash_token(raw), "p": purpose})).first()
        return row
```

`CoachProfileRepository.get_active(*, trainer_id, user_id)`; `TrainerPlayerLinkRepository.list_for_trainer(*, trainer_id, ...)`, `upsert_active(*, trainer_id, player_profile_id, source, share_link_id)`, `deactivate(*, trainer_id, player_profile_id)`; `ShareLinkRepository.lock_by_code(code)` (`SELECT ... FOR UPDATE`, documented tenancy exception because the code is the capability) and `increment_use(id)` as `UPDATE ... SET use_count = use_count + 1 WHERE id = :id AND (max_uses IS NULL OR use_count < max_uses)` returning rowcount.

**Step 4:** Run: `uv run pytest tests/repositories` Expected: PASS

**Step 5:** `git add app/repositories tests/repositories && git commit -m "feat(repositories): add identity repositories with tenant-scoped signatures"`

---

## Phase C: Identity contract

### Task 11: `api/deps.py` (the contract every epic consumes)

Spec: architecture §2 (read it fully; signatures are a cross-epic API).

**Files:**
- Create: `app/api/__init__.py`, `app/api/deps.py`, `app/main.py` (factory stub), `app/services/ports.py`
- Test: `tests/api/test_deps.py`

**Step 1: Write the failing test.** Build a tiny test app in `tests/api/conftest.py` mounting probe routes `/probe/principal`, `/probe/trainer-only`, `/probe/tenant`, `/probe/admin`, `/probe/password-change`, each returning the dependency's value. Use the real DB and `principal_token`.

```python
async def test_missing_or_bad_token_is_401_invalid_token(client) -> None:
    assert (await client.get("/probe/principal")).status_code == 401
    r = await client.get("/probe/principal", headers={"Authorization": "Bearer junk"})
    assert r.json()["error"]["code"] == "INVALID_TOKEN"

async def test_inactive_user_rejected_immediately(client, make_user, principal_token, db_session) -> None:
    u = await make_user(role="trainer")
    tok = principal_token(u)
    assert (await client.get("/probe/principal", headers=bearer(tok))).status_code == 200
    u.status = "inactive"; await db_session.flush()
    assert (await client.get("/probe/principal", headers=bearer(tok))).status_code == 401      # A-03

async def test_coach_moved_tenant_token_is_stale(client, make_trainer, make_coach, principal_token, db_session) -> None:
    ...  # token carries old tid; DB active tenure points elsewhere => 401 INVALID_TOKEN

async def test_deactivated_trainer_tenant_is_404_for_coach(client, ...) -> None: ...        # G-15

async def test_require_role_403(client, make_user, principal_token) -> None:
    coach_tok = principal_token(await make_user(role="coach"))
    assert (await client.get("/probe/trainer-only", headers=bearer(coach_tok))).status_code == 403

async def test_player_context_requires_active_link_else_404(client, make_player, make_trainer, principal_token) -> None:
    ...  # no link => 404 NOT_FOUND (never 403); with link => 200 and player_profile_ids populated
    ...  # child: only its own profile in player_profile_ids

async def test_super_admin_scope_rejects_impersonation(client, ...) -> None: ...            # act set => 403

async def test_x_impersonated_by_header_and_expired_session_401(client, ...) -> None: ...   # Task 12 adds middleware; here only 401 IMPERSONATION_EXPIRED
```

**Step 2:** Run: `uv run pytest tests/api/test_deps.py` Expected: FAIL ImportError

**Step 3: Implementation.** Implement exactly the table in architecture §2:

```python
# app/api/deps.py (excerpt; implement every row of the table)
@dataclass(frozen=True, slots=True)
class Principal:
    user_id: UUID; role: Role; trainer_id: UUID | None; act: UUID | None; child: bool; jti: UUID

async def _resolve(session: AsyncSession, claims: dict, now: datetime) -> PrincipalRow:
    # ONE statement: users u LEFT JOIN coach_profiles cp (active) LEFT JOIN users t (tenant)
    #                LEFT JOIN impersonation_sessions s ON s.jti = :jti AND :has_act
    ...

async def current_principal(request: Request, session: SessionDep, issuer: IssuerDep, clock: ClockDep) -> Principal:
    # No must_change_password gate: pending trainers cannot log in, and password/setup
    # activates + clears the flag atomically (password/change dropped 2026-10-18).
    return await _authenticate(request, session, issuer, clock)

PrincipalDep = Annotated[Principal, Depends(current_principal)]
```

Query rules (from the Phase B review, verified with `EXPLAIN`):
- Filter active coach tenures with the bare column (`cp.is_active` / `CoachProfile.is_active`), never `.is_(True)` / `IS true`: that disables the partial indexes `uq_coach_one_active_trainer` and `ix_coach_trainer_active` and falls back to a seq scan on every authenticated request.
- `impersonation_sessions.jti` is `uuid`: bind the token's `jti` as a `UUID` (`Principal.jti`), not `str(jti)`, or asyncpg raises a type error.
- Reuse `CoachProfileRepository` / `ImpersonationSessionRepository` query fragments where possible instead of re-writing the predicates.

Rules to encode: `status != active` => 401 `INVALID_TOKEN`; token `tid` differs from DB tenant (coach) => 401 `INVALID_TOKEN`; tenant trainer `status != active` => 404; `act` present and session missing/ended/expired => 401 `IMPERSONATION_EXPIRED`; `require_role(*roles)` => 403 `FORBIDDEN`; `require_tenant_scope` (trainer/coach => scope, player => `player_context`, super_admin => 403); `player_context(trainer_id: UUID = Query())` => 404 on any miss; `SuperAdminScope` rejects `act`; `IdempotencyDep`; `rate_limit(route_class)` dependency factory. `services/ports.py` defines `CommitmentChecker`, `PurchaseExecutor`, `CampSubmissionReader` Protocols plus `FakeCommitmentChecker`, `FakePurchaseExecutor`, `FakeCampSubmissionReader` (recording, configurable to fail) in `tests/fakes.py`.

**Step 4:** Run: `uv run pytest tests/api/test_deps.py` Expected: PASS

**Step 5:** `git add app/api app/main.py app/services/ports.py tests && git commit -m "feat(api): add principal/tenant dependency contract"`

---

### Task 12: Middleware (correlation id, impersonation header and audit) and app factory

**Files:** Create `app/api/middleware.py`, `app/services/audit_service.py`, `app/services/feature_flag_service.py`; Modify `app/main.py`; Test `tests/api/test_middleware.py`

**Step 1: Write the failing test**

```python
async def test_correlation_id_always_present_and_echoed(client) -> None:
    r = await client.get("/api/v1/public/trainers/none/branding", headers={"X-Correlation-Id": "abc"})
    assert r.headers["X-Correlation-Id"] == "abc"
    assert (await client.get("/healthz")).headers["X-Correlation-Id"]

async def test_impersonated_request_sets_header_and_writes_audit(client, impersonation_token, db_session) -> None:
    r = await client.get("/api/v1/me", headers=bearer(impersonation_token.access))
    assert r.headers["X-Impersonated-By"] == str(impersonation_token.admin_id)
    rows = (await db_session.execute(text("select method, path from audit_log where act_user_id is not null"))).all()
    assert ("GET", "/api/v1/me") in rows

async def test_normal_request_has_no_impersonation_header(client, trainer_token) -> None:
    assert "X-Impersonated-By" not in (await client.get("/api/v1/me", headers=bearer(trainer_token))).headers

async def test_unhandled_error_logged_once_with_correlation_id(settings) -> None:
    app = create_app(settings)

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("boom")

    # default ASGITransport re-raises app exceptions: the test errors if the exception escapes the middleware
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        with structlog.testing.capture_logs() as logs:
            r = await c.get("/boom", headers={"X-Correlation-Id": "abc"})
    assert r.status_code == 500
    assert r.json()["error"]["code"] == "INTERNAL_ERROR"
    assert r.headers["X-Correlation-Id"] == "abc"
    assert [e["event"] for e in logs].count("unhandled_exception") == 1
```

**Step 2:** Run: `uv run pytest tests/api/test_middleware.py` Expected: FAIL (routes absent)

**Step 3: Implementation.** `create_app(settings)` in `main.py` registers `install_error_handlers`, middleware, routers `auth, me, trainer, public, admin` under `/api/v1`, `/healthz`, structlog config with a redaction processor that drops keys matching `password|token|authorization|secret`. Middleware order: correlation id outermost. The correlation-id middleware wraps `call_next` in `try/except Exception`: on an unhandled exception it logs `unhandled_exception` once through structlog (redacted) and returns the `500 INTERNAL_ERROR` envelope with `X-Correlation-Id` set. It must not re-raise, so Starlette's `ServerErrorMiddleware` and uvicorn never see the exception (otherwise the traceback is logged a second time, unredacted, and the 500 has no correlation id). Remove the catch-all `Exception` handler from `install_error_handlers` (keep `_internal_error()` for the unmapped `IntegrityError` path) so there is a single 500 path; update `tests/core/test_errors.py` accordingly. Impersonation hook reads `request.state.principal` after the route resolves and writes `AuditService.record(action="impersonated_request", method=..., path=...)`.

**Step 4:** Run: the test file, then QG. Expected: PASS (the `/me` impersonation test passes after Task 17; mark it `xfail(strict=True)` until then, and remove the marker in Task 17).

**Step 5:** `git add app tests && git commit -m "feat(api): add middleware, audit/feature-flag stubs and app factory"`

---

## Phase D: Auth endpoints

Every endpoint task below follows the same loop. The schemas, status codes and error codes are in the spec section named in "Spec:". Put request/response models in `app/api/v1/<domain>/schemas.py` by copying the spec code blocks; shared models (`Role`, `Page`, `TokenPair`, `SessionUser`, `AvailabilitySet`, ...) go in `app/api/v1/common.py` once.

### Task 13: Login, refresh, logout

Spec: `POST /auth/login`, `/auth/refresh`, `/auth/logout`; brainstorm "login" and "refresh" flows.

**Files:**
- Create: `app/api/v1/common.py`, `app/api/v1/auth/{__init__,routes,schemas}.py`, `app/services/auth_service.py`
- Test: `tests/api/auth/test_login_refresh_logout.py`, `tests/services/test_auth_refresh.py`

**Step 1: Write the failing tests**

```python
async def test_login_returns_pair_and_session_user(client, make_trainer) -> None:
    t = await make_trainer(email="t@x.com", password=PW)
    r = await client.post("/api/v1/auth/login", json={"email": "t@x.com", "password": PW})
    body = r.json()
    assert r.status_code == 200 and body["user"]["role"] == "trainer" and body["user"]["trainer_id"] == str(t.id)
    assert body["tokens"]["expires_in"] == 900 and body["tokens"]["token_type"] == "bearer"
    assert "password_hash" not in r.text

@pytest.mark.parametrize("state", ["unknown", "wrong_password", "pending", "inactive", "deleted"])
async def test_login_failure_is_one_identical_401(client, state, make_user) -> None:
    ...  # all five produce status 401, code INVALID_CREDENTIALS, identical body

async def test_login_rate_limit_5_per_minute_per_email(client, make_user) -> None:
    ...  # 5 bad attempts then 429 with Retry-After

async def test_login_sets_last_login_and_child_flag(client, make_dependent) -> None: ...

async def test_refresh_rotates_and_rebuilds_claims(client, ...) -> None: ...
async def test_refresh_reuse_revokes_family(client, ...) -> None:
    ...  # use token A -> B issued; replay A -> 401 REFRESH_REUSE_DETECTED; B now 401 INVALID_REFRESH_TOKEN
async def test_refresh_expiry_boundary_30_days(client, clock, ...) -> None: ...   # 30d-1s ok, 30d+1s 401
async def test_refresh_parallel_same_token_one_success(committed_client, ...) -> None: ...  # marks concurrency
async def test_refresh_unknown_token_is_invalid_refresh_token(client) -> None: ...
async def test_logout_revokes_family_204_and_foreign_token_404(client, ...) -> None: ...
async def test_logout_allowed_while_must_change_password(client, ...) -> None: ...
```

**Step 2:** Run: `uv run pytest tests/api/auth/test_login_refresh_logout.py` Expected: FAIL 404 route not found

**Step 3: Implementation.** `AuthService.login` implements the brainstorm flow verbatim (rate-limit IP+email, `get_by_email`, `hasher.verify(user.password_hash if user else None, pwd)`, single `INVALID_CREDENTIALS` failure, `needs_rehash` upgrade, compute `trainer_id` and `child`, issue access + refresh family, set `last_login_at = clock.now()`). `refresh` runs inside `async with session.begin()`: `lock_by_hash` `FOR UPDATE`; if `consumed_at` set => revoke family, write audit row, commit, raise `REFRESH_REUSE_DETECTED` (the revoke must commit before raising: raise after the transaction block); if impersonation family, cap new expiry at `impersonation_sessions.expires_at` (G-14). Route handlers stay thin: call service, return schema.

**Step 4:** Run: the test file, `uv run pytest tests/services/test_auth_refresh.py -m concurrency`. Expected: PASS

**Step 5:** `git add app tests && git commit -m "feat(auth): add login, refresh rotation with reuse detection, logout"`

---

### Task 14: Password reset, verify email, setup

Spec: the five `/auth/password-reset/*`, `/auth/verify-email*`, `/auth/password/setup` sections (`/auth/password/change` dropped 2026-10-18). Jobs are enqueued through a `JobQueue` Protocol (`core/jobs.py`; `FakeJobQueue` in tests records `(name, kwargs)`; ARQ adapter in Task 25).

**Files:** Modify `app/services/auth_service.py`, `app/api/v1/auth/{routes,schemas}.py`; Create `app/core/jobs.py`; Test `tests/api/auth/test_passwords_and_verification.py`

**Step 1: Write the failing tests**

```python
async def test_reset_request_is_202_for_unknown_and_known_email(client, make_user, jobs) -> None:
    for email in ("nobody@x.com", make_user_email):
        r = await client.post("/api/v1/auth/password-reset/request", json={"email": email})
        assert r.status_code == 202 and r.json() == {"status": "accepted"}
    assert [j.name for j in jobs.enqueued] == ["send_password_reset"]       # only the real user

async def test_reset_request_rate_limited_but_still_202_shape(client) -> None: ...   # 6th => 429 per spec

async def test_reset_confirm_happy_path_revokes_all_families(client, login_pair, email) -> None: ...
async def test_reset_confirm_weak_password_does_not_burn_token(client, issued_token) -> None:
    r = await client.post(URL, json={"token": issued_token, "new_password": "password1234"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "WEAK_PASSWORD"
    ok = await client.post(URL, json={"token": issued_token, "new_password": STRONG})
    assert ok.status_code == 204
async def test_reset_confirm_reuse_expired_unknown_all_400_token_invalid(client, clock, ...) -> None: ...  # 1h boundary
async def test_verify_email_204_then_400_on_reuse(client, ...) -> None: ...
async def test_verify_resend_202_then_already_verified_200_and_limit_3_per_hour(client, ...) -> None: ...
async def test_password_setup_activates_pending_trainer(client, provisioned_trainer) -> None:
    ...  # pending -> active, must_change_password False, email verified, 200 AuthResponse; 24h boundary
async def test_password_setup_weak_password_does_not_burn_token(client, provisioned_trainer) -> None: ...
```

**Step 2:** Run: `uv run pytest tests/api/auth/test_passwords_and_verification.py` Expected: FAIL 404

**Step 3: Implementation.** Order inside the service for confirm/setup: (1) `PasswordPolicy.validate` (no DB write), (2) in one transaction `OneTimeTokenRepository.consume`, apply side effect, revoke families. Token failure => `BadRequestError("TOKEN_INVALID")` (always the same message). `request_reset` always returns the same body and only enqueues when the user is `active`; the rate-limit key is the lowercased input email. Phase D review (user decisions 2026-10-18): also apply the default per-IP limit; enqueue failures after commit are logged (`job_enqueue_failed`), not 500; timing difference between known and unknown emails is an accepted risk; refresh reuse stays strict (no grace window). Raw tokens go only into the job kwargs (`token_id` plus raw token to the email adapter in-process; never logged).

**Step 4:** Run the file. Expected: PASS

**Step 5:** `git add app tests && git commit -m "feat(auth): add password reset, email verification and setup"`

---

### Task 15: Register (static player link, coach_unique, authenticated redeem)

Spec: `POST /auth/register`; brainstorm "coach_unique redemption" flow. Needs share-link creation to exist for realistic tests; tests may insert links through the `add_share_link` factory, so no dependency on Task 21.

**Files:** Create `app/services/sharelink_service.py`; Modify auth routes/service; Test `tests/api/auth/test_register.py`, `tests/concurrency/test_share_link_redemption.py`

**Step 1: Write the failing tests**

```python
async def test_static_link_registers_player_with_profile_and_link(client, static_link) -> None:
    r = await client.post(f"/api/v1/auth/register?share_code={static_link.code}", json=REG_BODY)
    assert r.status_code == 201 and r.json()["user"]["role"] == "player"
    # asserts: player_profile exists (is_dependent False), trainer_player_links(source=share_link, status=active), use_count+1

async def test_exactly_one_of_share_code_or_camp_submission(client) -> None:
    assert (await client.post("/api/v1/auth/register", json=REG_BODY)).status_code == 422
    assert (await client.post(f"/api/v1/auth/register?share_code=a&camp_submission_id={uuid4()}", json=REG_BODY)).status_code == 422

@pytest.mark.parametrize("state", ["unknown", "inactive", "expired", "exhausted", "trainer_deactivated"])
async def test_invalid_link_states_all_404_share_link_invalid(client, state, ...) -> None: ...

async def test_coach_link_creates_coach_with_active_tenure(client, coach_link) -> None: ...
async def test_coach_link_email_mismatch_403(client, coach_link) -> None: ...
async def test_coach_link_existing_account_409_wrong_link_kind(client, coach_link, make_user) -> None: ...
async def test_authenticated_coach_move_conflict_409_names_current_trainer(client, ...) -> None: ...
async def test_authenticated_existing_player_redeem_links_only_200(client, ...) -> None: ...
async def test_child_cannot_redeem_403_and_unverified_403(client, ...) -> None: ...
async def test_duplicate_email_409_account_exists_with_next_hint(client, static_link, make_user) -> None: ...
async def test_parent_registers_existing_dependents_for_static_link(client, ...) -> None: ...
async def test_empty_player_profile_ids_422(client, ...) -> None: ...
async def test_coach_link_expiry_boundary_7_days(client, clock, ...) -> None: ...
async def test_register_rate_limit_20_per_hour_per_ip(client, ...) -> None: ...
async def test_blocked_unverified_parent_enqueues_notification(client, ...) -> None: ...   # US-01.06 job
```

```python
# tests/concurrency/test_share_link_redemption.py
@pytest.mark.gate
@pytest.mark.concurrency
async def test_20_way_coach_unique_redemption_one_success(committed_app, coach_link) -> None:
    results = await asyncio.gather(*[register(committed_app, coach_link.code, f"c{i}@x.com") for i in range(20)])
    codes = sorted(r.status_code for r in results)
    assert codes == [201] + [404] * 19

@pytest.mark.concurrency
async def test_200_way_static_registration_no_lost_writes(committed_app, static_link) -> None:
    ...  # 200 users created, use_count == 200
```

**Step 2:** Run: `uv run pytest tests/api/auth/test_register.py tests/concurrency` Expected: FAIL 404

**Step 3: Implementation.** `ShareLinkService.redeem` follows the brainstorm flow in a single transaction: `lock_by_code` (`FOR UPDATE`) => validity checks (active, not expired, uses left, trainer active) => kind-specific branch => `increment_use` (rowcount 0 => `NOT_FOUND`-equivalent `SHARE_LINK_INVALID` 404) => `share_link_events(registered)` => commit => enqueue welcome + verification jobs. Map `uq_users_email` on a static link to `ACCOUNT_EXISTS` with `details=[{"next": "login_then_redeem"}]`: catch `EmailTakenError` in the service and re-raise with the details. Query exclusivity validated in the route via a dependency raising 422. Coach move (user decision 2026-10-18): `current_principal` rejects a coach without an active tenure, so add `current_principal_any_tenure` (architecture §3 deps table) and use it only on `POST /auth/register`; every other route keeps the 401. Only `optional_principal_any_tenure` is needed (the route is public or authenticated). Register body (user decision 2026-10-18): email/password/names are optional in the schema and required only for a new account; an authenticated redeem with only `player_profile_ids` => 200. An already active link => no `use_count` increment, no event. Birth date (user decision 2026-10-18): `RegisterRequest.birth_date` is required when a static link creates a new player account (missing => 422 `VALIDATION_ERROR`, future date => 422), ignored for coach links; the registrant's own profile stores it with gender `undisclosed`. No sentinel dates. Test: a coach whose tenure was closed logs in, redeems a `coach_unique` link of another trainer => new active row; the same token on any other route => 401.

**Step 4:** Run: both files, `uv run pytest -m gate`. Expected: PASS

**Step 5:** `git add app tests && git commit -m "feat(auth): add register via share link with locked redemption"`

---

### Task 16: Impersonation exit

Spec: `POST /auth/impersonation/exit`. Impersonation start is Task 24.

**Files:** Create `app/services/impersonation_service.py`; Modify auth routes; Test `tests/api/auth/test_impersonation_exit.py`

**Step 1:** Failing tests: `exit` with an `act` token => 204 and the same token now 401 `IMPERSONATION_EXPIRED`; refresh of the impersonation family after exit => 401; non-impersonating principal => 403 `FORBIDDEN`; expired session (FakeClock +60 min+1s) => 401 `IMPERSONATION_EXPIRED`.
**Step 2:** Run: `uv run pytest tests/api/auth/test_impersonation_exit.py` Expected: FAIL 404
**Step 3:** `ImpersonationService.exit(principal)` sets `ended_at`, revokes the family tied to the session (`family_id == session.jti`, see architecture §5 Impersonation). Fixture `impersonation_token` creates session + tokens directly via repositories.
**Step 4:** Run the file. Expected: PASS
**Step 5:** `git add app tests && git commit -m "feat(auth): add impersonation exit"`

---

## Phase E: Me, players, approvals

### Task 17: `GET/PATCH /me`, avatar presign/confirm, email change

Spec: Domain Me sections `GET /me` through `POST /me/email-change/confirm`.

**Files:** Create `app/api/v1/me/{__init__,routes,schemas}.py`, `app/services/user_service.py`; Test `tests/api/me/test_me.py`, `test_avatar.py`, `test_email_change.py`

**Step 1: Write the failing tests**

```python
async def test_get_me_shapes_for_each_role(client, ...) -> None: ...         # coach has `coach` block, others None
async def test_get_me_404_when_coach_tenant_deactivated(client, ...) -> None: ...
async def test_get_me_works_while_must_change_password(client, ...) -> None: ...
async def test_get_me_impersonated_shows_impersonated_by(client, impersonation_token) -> None: ...
async def test_patch_me_updates_profile_and_ignores_nothing_unknown(client, ...) -> None:
    r = await client.patch("/api/v1/me", json={"first_name": "A", "role": "super_admin"}, headers=...)
    assert r.status_code == 422                                              # extra=forbid
async def test_patch_me_coach_fields_forbidden_for_player_422_field_not_allowed(client, ...) -> None: ...
async def test_patch_me_coach_updates_public_profile(client, ...) -> None: ...
async def test_patch_me_bad_timezone_422(client, ...) -> None: ...
async def test_avatar_presign_constraints_and_server_chosen_key(client, storage) -> None:
    r = await client.put("/api/v1/me/avatar", json={"content_type": "image/png", "content_length": 1000}, headers=...)
    assert r.json()["key"].startswith("avatars/")
    bad = await client.put("/api/v1/me/avatar", json={"content_type": "image/gif", "content_length": 1000}, headers=...)
    assert bad.status_code == 422
async def test_avatar_over_5mb_422(client, ...) -> None: ...
async def test_avatar_confirm_head_checks_then_persists(client, storage, ...) -> None: ...
async def test_avatar_confirm_missing_key_404_wrong_type_invalid_avatar(client, storage, ...) -> None: ...
async def test_email_change_issues_token_to_new_address_old_stays_active(client, email, ...) -> None: ...
async def test_email_change_wrong_password_403(client, ...) -> None: ...
async def test_email_change_confirm_swaps_and_revokes_families(client, ...) -> None: ...
async def test_email_change_confirm_conflict_409_and_token_not_consumed(client, ...) -> None: ...
async def test_get_me_p95_under_100ms_smoke(client, ...) -> None: ...        # 50 sequential calls, loose bound, marks `perf`
```

**Step 2:** Run: `uv run pytest tests/api/me` Expected: FAIL 404

**Step 3: Implementation.** `PATCH /me` uses `model_fields_set` to detect coach-only fields and raises `UnprocessableError("FIELD_NOT_ALLOWED", ..., details=[{"field": name, ...}])` for non-coaches. Presign: key `f"avatars/{new_id()}"` via `ObjectStorage.presign_put`. Confirm: `storage.head(key)` => `None` => `NotFoundError`; type/size mismatch => `UnprocessableError("INVALID_AVATAR")`. `email_change_confirm` consumes the token, swaps `users.email` and revokes families in one transaction; an `IntegrityError` on `uq_users_email` rolls back the consume (handled by the global mapper). Remove the `xfail` from Task 12.

**Step 4:** Run: `uv run pytest tests/api/me tests/api/test_middleware.py` Expected: PASS

**Step 5:** `git add app tests && git commit -m "feat(me): add profile, avatar and email change endpoints"`

---

### Task 18: Availability (`/me/availability`, dependent variant)

> **Review decision (2026-10-19):** dependent availability uses the parent's timezone; a different `timezone` => 422; never write the parent's profile.

Spec: `GET/PUT /me/availability`, `PUT /me/players/{id}/availability`; `AvailabilityService.check_conflict` is a pure function.

**Files:** Create `app/services/availability_service.py`; Modify me routes; Test `tests/services/test_availability_conflict.py`, `tests/api/me/test_availability.py`

**Step 1: Write the failing tests**

```python
# pure unit test, minute boundaries
@pytest.mark.parametrize(("a", "b", "expected"), [
    (("09:00", "10:00"), ("10:00", "11:00"), False),
    (("09:00", "10:00"), ("09:59", "11:00"), True),
    (("09:00", "10:00"), ("08:00", "09:00"), False),
])
def test_check_conflict_half_open(a, b, expected) -> None:
    assert check_conflict([window(*a)], window(*b)) is expected

async def test_put_replaces_whole_week_atomically(client, ...) -> None: ...
async def test_put_overlap_422_overlapping_windows_and_old_set_intact(client, ...) -> None: ...
async def test_put_start_not_before_end_422(client, ...) -> None: ...
async def test_put_over_70_windows_422(client, ...) -> None: ...
async def test_trainer_and_super_admin_403(client, ...) -> None: ...
async def test_dependent_availability_other_owner_404_child_403(client, ...) -> None: ...
```

**Step 2:** Run: `uv run pytest tests/services/test_availability_conflict.py tests/api/me/test_availability.py` Expected: FAIL ImportError

**Step 3: Implementation.** `replace` does `DELETE` then `INSERT` inside the service transaction; the exclusion constraint raises and the whole transaction rolls back (old set intact). Validate `zoneinfo.ZoneInfo(tz)` in the schema validator; `start < end` in `WeeklyWindow` validator (422 `VALIDATION_ERROR`).

**Step 4:** Run the files. Expected: PASS. **Step 5:** `git add app tests && git commit -m "feat(me): add weekly availability endpoints"`

---

### Task 19: Dependents `/me/players*` and `GET /me/contexts`

Spec: Dependents section and `GET /me/contexts`.

**Files:** Create `app/services/player_service.py`; Modify me routes/schemas; Test `tests/api/me/test_players.py`, `tests/services/test_player_age.py`

**Step 1: Write the failing tests**

```python
@pytest.mark.parametrize(("birth", "ok"), [("2008-10-18", False),  # turns 18 today on FakeClock (2026-10-18) => rejected
                                          ("2008-10-19", True),    # 17, turns 18 tomorrow => allowed
                                          ("2025-10-18", True),    # exactly 1
                                          ("2025-10-19", False)])  # not yet 1
def test_dependent_age_rule_1_to_17(birth, ok, clock) -> None: ...

async def test_create_dependent_with_child_login_creates_active_preverified_user(client, ...) -> None: ...
async def test_create_dependent_trainer_not_linked_404(client, ...) -> None: ...
async def test_duplicate_player_409_unless_allow_duplicate(client, ...) -> None: ...
async def test_child_login_email_taken_409_and_weak_password_422(client, ...) -> None: ...
async def test_child_cannot_create_dependent_403(client, ...) -> None: ...
async def test_list_players_own_first_then_dependents(client, ...) -> None: ...
async def test_get_player_not_owner_404_child_reads_only_self(client, ...) -> None: ...
async def test_patch_login_absent_unchanged_null_revokes_object_replaces(client, ...) -> None:
    ...  # uses model_fields_set; null => login user inactive, families revoked, login_user_id retained
async def test_delete_dependent_soft_removes_and_keeps_history(client, ...) -> None: ...
async def test_delete_dependent_with_commitments_409_lists_rsvp_ids(client, fake_commitments) -> None: ...
async def test_delete_own_profile_409_cannot_remove_own_profile(client, ...) -> None: ...
async def test_add_trainer_link_201_then_200_idempotent_and_reactivates(client, ...) -> None: ...
async def test_remove_trainer_link_soft_and_commitments_409(client, ...) -> None: ...
async def test_contexts_lists_active_links_omits_deactivated_trainers(client, ...) -> None: ...
async def test_contexts_non_player_and_child_403(client, ...) -> None: ...
```

**Step 2:** Run: `uv run pytest tests/api/me/test_players.py tests/services/test_player_age.py` Expected: FAIL

**Step 3: Implementation.** Age is computed from `Clock.today()`: `age = today.year - b.year - ((today.month, today.day) < (b.month, b.day))`; allowed `1 <= age < 18` (spec §10; user decision 2026-10-18), else `UnprocessableError("INVALID_AGE")`. The test table above must match exactly this rule. Child login creation uses `PasswordPolicy`, `users(role=player, status=active, is_email_verified=True)`. `DELETE` calls `CommitmentChecker.future_paid_rsvps(player_profile_id, trainer_id)` per trainer; non-empty => `ConflictError("ACTIVE_COMMITMENTS", details=[{"rsvp_ids": [...]}])`. Ownership lookups go through `PlayerProfileRepository.get_owned(owner_user_id=, id=)` (None => 404).

**Step 4:** Run the files. Expected: PASS. **Step 5:** `git add app tests && git commit -m "feat(me): add dependents, child login, trainer links and contexts"`

---

### Task 20: Parent approvals

> **Review decision (2026-10-19):** approve re-checks dependent profile and trainer link are active, else 404 `NOT_FOUND`, no executor call; deny still allowed.

Spec: Approvals section; Conventions (Idempotency). `PurchaseExecutor` is a Protocol with a fake.

**Files:** Create `app/services/approval_service.py`; Modify `app/api/deps.py` (`IdempotencyDep` body), me routes; Test `tests/api/me/test_approvals.py`, `tests/concurrency/test_approve_parallel.py`

**Step 1: Write the failing tests**

```python
async def test_list_requires_trainer_id_and_valid_context_404(client, ...) -> None: ...
async def test_list_only_callers_dependents_approvals_filtered_by_status(client, ...) -> None: ...
async def test_approve_executes_once_and_returns_approved(client, executor, pending_approval) -> None:
    r = await client.post(URL, headers={**auth, "Idempotency-Key": "k1"})
    assert r.status_code == 200 and r.json()["status"] == "approved" and executor.calls == 1
async def test_approve_replay_same_key_returns_stored_response_no_second_effect(client, executor, ...) -> None: ...
async def test_approve_same_key_different_body_409_reused(client, ...) -> None: ...   # deny with another note
async def test_approve_missing_key_400(client, ...) -> None: ...
async def test_approve_not_pending_409(client, ...) -> None: ...                       # already decided or expired
async def test_approve_other_parents_approval_404(client, ...) -> None: ...
async def test_executor_failure_rolls_back_decision(client, failing_executor, ...) -> None: ...
async def test_deny_records_note_and_enqueues_child_notification(client, jobs, ...) -> None: ...
async def test_approval_expires_after_48h_boundary(clock, ...) -> None: ...            # service-level
```

```python
@pytest.mark.gate
@pytest.mark.concurrency
async def test_10_way_parallel_approve_one_effect(committed_app, executor, pending_approval) -> None:
    rs = await asyncio.gather(*[approve(committed_app, pending_approval.id, key=f"k{i}") for i in range(10)])
    assert sorted(r.status_code for r in rs).count(200) == 1 and executor.calls == 1
    assert all(r.status_code in (200, 409) for r in rs)
```

**Step 2:** Run: `uv run pytest tests/api/me/test_approvals.py tests/concurrency/test_approve_parallel.py` Expected: FAIL 404

**Step 3: Implementation.** `ApprovalService.decide` opens the transaction, `lock_by_id_for_parent(id, parent_user_id)` (`FOR UPDATE`; `None` => 404), `status != pending or expires_at <= now` => `APPROVAL_NOT_PENDING`, then `PurchaseExecutor.execute(approval)` in the same transaction, set status/`responded_at`. The idempotency dependency wraps this: look up `(key, principal_id)`; same hash => replay stored status+body; different hash => 409 `IDEMPOTENCY_KEY_REUSED`; store response in the same transaction as the decision so a rollback stores nothing.

**Step 4:** Run the files. Expected: PASS. **Step 5:** `git add app tests && git commit -m "feat(me): add parent approvals with idempotency"`

---

## Phase F: Trainer, public, admin

### Task 21: Trainer share links and availability reads

Spec: Trainer domain, share-link and availability endpoints.

**Files:** Create `app/api/v1/trainer/{__init__,routes,schemas}.py`; Modify `app/services/sharelink_service.py`, `availability_service.py`; Test `tests/api/trainer/test_share_links.py`, `test_availability_reads.py`

**Step 1: Write the failing tests**

```python
async def test_trainer_creates_static_and_coach_unique_links(client, ...) -> None:
    ...  # coach_unique: expires_at = now + 7d, max_uses 1, url uses settings.public_base_url
async def test_coach_creates_static_only_coach_unique_403(client, ...) -> None: ...
async def test_target_email_required_iff_coach_unique_422(client, ...) -> None: ...
async def test_coach_unique_for_active_coach_elsewhere_409_names_trainer(client, ...) -> None: ...
async def test_list_links_filters_and_cursor_pagination_own_tenant_only(client, ...) -> None: ...
async def test_delete_link_soft_deactivates_idempotent_other_tenant_404(client, ...) -> None: ...
async def test_trainer_reads_linked_player_availability_else_404(client, ...) -> None: ...   # inactive link => 404
async def test_trainer_reads_active_coach_availability_former_coach_404_coach_role_403(client, ...) -> None: ...
```

**Step 2:** Run: `uv run pytest tests/api/trainer` Expected: FAIL 404

**Step 3: Implementation.** All routes take `TenantDep`; the service receives `scope.trainer_id`. Share code is `new_opaque_token()` (URL-safe). `GET /trainer/players/{id}/availability` joins `trainer_player_links` with `trainer_id=` (the tenancy exception, architecture §2). `coach_unique` role check uses `require_role(Role.trainer)` inside the service when `kind == coach_unique` (coach => `ForbiddenError`).

**Step 4:** Run: `uv run pytest tests/api/trainer`. Expected: PASS. **Step 5:** `git add app tests && git commit -m "feat(trainer): add share links and availability reads"`

---

### Task 22: Trainer branding and logo upload

Spec: `GET/PATCH /trainer/branding`, logo presign/confirm. `BrandingService` also serves Task 23.

**Files:** Create `app/services/branding_service.py`; Modify trainer routes; Test `tests/api/trainer/test_branding.py`

**Step 1: Write the failing tests**

```python
async def test_branding_get_for_trainer_and_coach_patch_trainer_only(client, ...) -> None: ...
async def test_patch_branding_hex_regex_422(client, ...) -> None: ...
async def test_logo_presign_type_and_2mb_limit(client, ...) -> None: ...
async def test_logo_confirm_png_persists_key(client, storage, ...) -> None: ...
async def test_logo_confirm_svg_is_sanitised_and_reserialised(client, storage, ...) -> None:
    storage.put("logos/x", b'<svg xmlns="http://www.w3.org/2000/svg"><script/></svg>', "image/svg+xml")
    ...  # stored object no longer contains <script
async def test_logo_confirm_hostile_svg_xxe_422_invalid_logo(client, storage, ...) -> None: ...
async def test_logo_confirm_missing_key_404_coach_403(client, ...) -> None: ...
```

**Step 2:** Run: `uv run pytest tests/api/trainer/test_branding.py` Expected: FAIL 404

**Step 3: Implementation.** Mirror avatar flow with `logos/{uuid7}`; on confirm for `image/svg+xml`, `storage.get(key)` -> `sanitize_svg` -> `storage.put(key, clean, "image/svg+xml")` before persisting `logo_key`. `logo_url` is built by a `MediaUrlBuilder` (settings) from the key.

**Step 4:** Run the file. Expected: PASS. **Step 5:** `git add app tests && git commit -m "feat(trainer): add branding and logo upload with SVG sanitising"`

---

### Task 23: Public endpoints

Spec: Public domain.

**Files:** Create `app/api/v1/public/{__init__,routes,schemas}.py`; Modify `sharelink_service.py`, `branding_service.py`; Test `tests/api/public/test_public.py`

**Step 1: Write the failing tests**

```python
async def test_preview_valid_static_and_coach_unique_with_email_hint(client, ...) -> None:
    ...  # coach_unique: target_email_hint == "s***@example.com"; never the full email
async def test_preview_records_opened_event_with_hashed_ip_and_ua(client, db_session, ...) -> None: ...
@pytest.mark.parametrize("state", ["unknown", "inactive", "expired", "exhausted", "trainer_deactivated"])
async def test_preview_invalid_all_404_share_link_invalid(client, state, ...) -> None: ...
async def test_preview_rate_limit_60_per_minute(client, ...) -> None: ...
async def test_public_branding_exposes_only_allowed_fields_with_cache_control(client, ...) -> None:
    r = await client.get("/api/v1/public/trainers/acme/branding")
    assert r.headers["Cache-Control"] == "public, max-age=300"
    assert set(r.json()) == {"business_name", "logo_url", "primary_color", "secondary_color"}
async def test_public_branding_unknown_or_deactivated_404(client, ...) -> None: ...
async def test_public_routes_need_no_token(client) -> None: ...
```

**Step 2:** Run: `uv run pytest tests/api/public` Expected: FAIL 404

**Step 3: Implementation.** No principal dependency (`OptionalPrincipalDep` is not needed). IP/UA hashing: `sha256(settings.hash_salt + value)`. Set `Cache-Control` only on the branding response.

**Step 4:** Run the file. Expected: PASS. **Step 5:** `git add app tests && git commit -m "feat(public): add share link preview and public branding"`

---

### Task 24: Admin directory, provisioning, lifecycle, erasure, impersonation start

> **Decisions (2026-10-19):** erasure generalises `birth_date` to January 1 of the birth year (see spec erase section for full scope); deactivating a `pending` user => 409 `INVALID_STATUS_TRANSITION`; `?trainer_id=` lists the trainer and its active coaches only; `coach_unique` creation for an already-assigned coach => 409 `COACH_ALREADY_ASSIGNED`.

Spec: Admin domain (all routes). Erasure rules: spec `DELETE /admin/users/{id}`, brainstorm Security (Privacy).

**Files:** Create `app/api/v1/admin/{__init__,routes,schemas}.py`, `app/services/erasure.py` (anonymiser); Modify `user_service.py`, `impersonation_service.py`; Test `tests/api/admin/test_users.py`, `test_provision.py`, `test_lifecycle.py`, `test_erasure.py`, `test_impersonate.py`, `tests/services/test_erasure_anonymiser.py`

**Step 1: Write the failing tests**

```python
async def test_directory_filters_keyset_order_and_trgm_query(client, admin_token, ...) -> None: ...
async def test_directory_query_min_2_chars_422(client, admin_token) -> None: ...
async def test_provision_trainer_creates_pending_user_profiles_and_invite_job(client, admin_token, jobs) -> None:
    ...  # 201 AdminUserDetail, status pending, must_change_password True, no password/token in body; job send_trainer_invite; audit trainer.created
async def test_provision_trainer_rolls_back_all_rows_on_slug_conflict(client, admin_token, ...) -> None: ...  # 409 SLUG_TAKEN, no users row left
async def test_provision_duplicate_email_409(client, admin_token, ...) -> None: ...
async def test_provisioned_trainer_cannot_login_until_setup(client, ...) -> None: ...      # 401 INVALID_CREDENTIALS
async def test_get_patch_user_and_immutable_role_email_422(client, admin_token, ...) -> None: ...
async def test_deactivate_revokes_families_and_blocks_next_request_immediately(client, ...) -> None: ...
async def test_deactivate_trainer_hides_tenant_404_for_coach_and_public(client, ...) -> None: ...  # G-15
async def test_deactivate_idempotent_already_inactive_200(client, ...) -> None: ...      # no self-guard (dropped 2026-10-18)
async def test_provision_trainer_accepts_phone_and_201_has_business_name_slug(client, admin_token, ...) -> None: ...
async def test_reactivate_transitions_and_pending_409_invalid_status_transition(client, ...) -> None: ...
async def test_erase_anonymises_in_place_and_writes_ledger(client, admin_token, ...) -> None: ...
async def test_erase_deletes_one_time_tokens_and_erases_dependents(client, ...) -> None: ...
async def test_erase_twice_409_already_erased(client, ...) -> None: ...                 # no self-guard (dropped 2026-10-18)
async def test_after_erasure_no_plaintext_email_in_any_text_or_jsonb_column(client, committed_engine, ...) -> None:
    ...  # information_schema scan of every text/varchar/citext/jsonb column across all tables, expect 0 hits for the original email
async def test_erased_user_cannot_be_updated_or_reactivated_409_user_erased(client, ...) -> None: ...
async def test_impersonate_returns_60min_token_with_act_and_session(client, admin_token, ...) -> None: ...
async def test_impersonate_super_admin_403_inactive_target_409(client, ...) -> None: ...
async def test_impersonating_admin_cannot_reach_admin_routes_403(client, ...) -> None: ...
async def test_impersonation_expiry_boundary_60_min(client, clock, ...) -> None: ...
async def test_every_admin_route_non_admin_403_anonymous_401(client, ...) -> None: ...
```

**Step 2:** Run: `uv run pytest tests/api/admin tests/services/test_erasure_anonymiser.py` Expected: FAIL 404

**Step 3: Implementation.**
- Every route depends on `SuperAdminScope`.
- `provision_trainer`: one transaction creating `users` (random 32-byte password hashed, `pending`, `must_change_password=True`), `profiles`, `trainer_profiles`, `trainer_setup` token (24 h), audit `trainer.created`, then enqueue `send_trainer_invite` after commit.
- Erasure anonymiser (pure, unit-tested): email => `deleted-{id}@erased.invalid`, names => `Deleted User`, phone/school/avatar_key null, `password_hash` => unusable hash, `status=deleted`, `deleted_at=now`; dependents (`owner_user_id`) anonymised in the same transaction; `share_links.target_email` scrubbed; `UserErasure(original_email_hash=sha256(lower(email)), legal_retention_until=now + config years)`.
- Impersonate: create `impersonation_sessions(expires_at=now+60m, jti)`, access token `act=admin`, `ttl=3600`, refresh family created with `family_id = session.jti` (the Task 13 refresh path relies on this link).
- Directory keyset on `(created_at, id)` with `core.pagination` cursors; `query` uses `ILIKE` against trigram-indexed expressions.

**Step 4:** Run: `uv run pytest tests/api/admin tests/services`. Expected: PASS. **Step 5:** `git add app tests && git commit -m "feat(admin): add directory, provisioning, lifecycle, erasure and impersonation"`

---

## Phase G: Jobs, cross-cutting gates, spec sync

### Task 25: Background jobs (ARQ)

Spec: requirements "Background jobs" table. Jobs call services only.

**Files:** Create `app/jobs/worker.py`, `app/jobs/identity_jobs.py`; Test `tests/jobs/test_identity_jobs.py`

**Step 1: Write the failing tests** (one per job at minimum)

```python
async def test_send_password_reset_sends_once_even_if_retried(ctx, issued_token_id, email) -> None:
    await send_password_reset(ctx, token_id=issued_token_id)
    await send_password_reset(ctx, token_id=issued_token_id)       # idempotent on token_id
    assert len(email.sent) == 1
async def test_job_skips_when_token_already_used(ctx, ...) -> None: ...                 # re-reads state by id
async def test_expire_purchase_approvals_marks_pending_past_expiry(ctx, clock, ...) -> None: ...
async def test_expire_impersonation_sessions_closes_expired(ctx, clock, ...) -> None: ...
async def test_purge_expired_tokens_deletes_expired_and_used_over_7_days(ctx, clock, ...) -> None: ...
async def test_worker_settings_retry_5_attempts_exponential_and_cron_registry() -> None:
    s = WorkerSettings
    assert s.max_tries == 5
    assert {c.name for c in s.cron_jobs} >= {"expire_purchase_approvals", "expire_impersonation_sessions", "purge_expired_tokens"}
async def test_dead_letter_logged_after_final_failure(ctx, caplog, ...) -> None: ...
```

Also cover `send_trainer_invite`, `send_welcome_email`, `send_verification_email`, `notify_parent_approval_request`, `notify_child_decision`, `notify_parent_share_link_blocked`.

**Step 2:** Run: `uv run pytest tests/jobs` Expected: FAIL ImportError

**Step 3: Implementation.** `WorkerSettings` (`functions`, `cron_jobs` every 5 min for the two expiry jobs, nightly purge, `max_tries=5`, `retry_jobs=True`, backoff `defer = 2 ** job_try`, `on_job_failure` hook writes a structured dead-letter log when `job_try == max_tries`). Provide `ArqJobQueue` implementing the `JobQueue` Protocol from Task 14 and wire it in `create_app`.

**Step 4:** Run: `uv run pytest tests/jobs`. Expected: PASS. **Step 5:** `git add app tests && git commit -m "feat(jobs): add ARQ worker, identity jobs and cron registry"`

---

### Task 26: Permission matrix, cross-tenant gate, meta-test

Spec: "Permission summary" table and brainstorm "Permission matrix as data".

**Files:** Create `tests/permissions/matrix.py`, `tests/permissions/test_matrix.py`, `tests/permissions/test_cross_tenant.py`

**Step 1: Write the failing test**

```python
# tests/permissions/test_matrix.py
from fastapi.routing import APIRoute
from tests.permissions.matrix import MATRIX          # list[(method, path_template, role, expected_status)]

def test_every_registered_route_is_in_the_matrix(app) -> None:
    registered = {(m, r.path) for r in app.routes if isinstance(r, APIRoute) and r.path.startswith("/api/v1")
                  for m in r.methods if m != "HEAD"}
    covered = {(m, p) for m, p, *_ in MATRIX}
    assert registered - covered == set(), f"routes missing from permission matrix: {sorted(registered - covered)}"

@pytest.mark.parametrize(("method", "path", "role", "expected"), MATRIX)
async def test_matrix_cell(method, path, role, expected, client, tokens, seeded) -> None:
    r = await client.request(method, fill(path, seeded), headers=tokens[role], json=minimal_body(method, path))
    assert r.status_code == expected
```

```python
# tests/permissions/test_cross_tenant.py
@pytest.mark.gate
@pytest.mark.parametrize(("method", "path"), TENANT_SCOPED_ROUTES)
async def test_other_tenants_ids_return_404(method, path, client, trainer_a_token, trainer_b_ids) -> None:
    r = await client.request(method, fill(path, trainer_b_ids), headers=trainer_a_token, json=minimal_body(method, path))
    assert r.status_code == 404
```

**Step 2:** Run: `uv run pytest tests/permissions` Expected: FAIL ImportError (`matrix` not written)

**Step 3: Implementation.** Encode the spec "Permission summary" table as data: one row per `(route, role)` cell. Every "no"/403 cell expects 403 (anonymous: 401); every "own tenant" cell has a paired 404 case using a second trainer's ids. Include child-forbidden cells. `minimal_body` returns the smallest valid body per route so authorization, not validation, decides the status. Any route missing from the matrix fails the meta-test.

**Step 4:** Run: `uv run pytest tests/permissions -m "not slow"` and `uv run pytest -m gate`. Expected: PASS

**Step 5:** `git add tests/permissions && git commit -m "test: add permission matrix, cross-tenant gate and route coverage meta-test"`

---

### Task 27: Rate-limit, time-boundary and privacy sweeps

**Files:** Create `tests/e2e/test_rate_limits.py`, `tests/e2e/test_time_boundaries.py`, `tests/e2e/test_privacy.py`, `tests/unit/test_argon2_benchmark.py`

**Step 1: Write the failing tests**

```python
@pytest.mark.parametrize(("method", "path", "body", "limit", "key_hint"), RATE_LIMITED_ROUTES)  # from spec: login 10/min ip + 5/min email,
async def test_route_returns_429_with_retry_after_after_limit(...) -> None: ...                 # register 20/h, reset 5/h, resend 3/h, preview 60/min

@pytest.mark.parametrize(("what", "ttl_seconds"), [("access", 900), ("refresh", 30*86400), ("impersonation", 3600),
                                                  ("password_reset", 3600), ("email_verify", 86400),
                                                  ("trainer_setup", 86400), ("coach_link", 7*86400), ("approval", 48*3600)])
async def test_ttl_boundaries_one_second_either_side(what, ttl_seconds, clock, ...) -> None: ...

async def test_no_response_schema_exposes_secret_fields(openapi) -> None:
    banned = {"password_hash", "token_hash", "ip_hash", "user_agent_hash", "legal_retention_until"}
    assert not banned & all_schema_property_names(openapi)

async def test_logs_never_contain_raw_tokens_or_passwords(client, caplog, ...) -> None: ...

def test_argon2_verify_takes_at_least_100ms_with_production_params() -> None:
    ...  # time one verify with Settings defaults; assert >= 0.1 s
```

**Step 2:** Run: `uv run pytest tests/e2e tests/unit/test_argon2_benchmark.py` Expected: FAIL for any behaviour not yet satisfied (fix in the owning service, not in the test)

**Step 3:** Fix every gap the sweeps reveal in the owning module; do not weaken assertions. `RATE_LIMITED_ROUTES` includes `/auth/refresh` (default, per IP) and `/auth/logout` (default, per principal) (user decision 2026-10-18).

**Step 4:** Run: `uv run pytest tests/e2e tests/unit`. Expected: PASS

**Step 5:** `git add app tests && git commit -m "test: add rate limit, TTL boundary and privacy sweeps"`

---

### Task 28: OpenAPI conformance with the spec, load scenarios, docs

**Files:**
- Create: `tests/test_openapi_contract.py`, `tests/load/seed_10k_users.py`, `tests/load/scenarios.js` (k6), `README.md` section
- Modify: `specs/api-designer-spec.md` only to record deltas found during implementation (append under the existing "Assumptions to confirm"; never rewrite endpoint sections without the `/api-designer` skill)

**Step 1: Write the failing test**

```python
# tests/test_openapi_contract.py
import re
from pathlib import Path

SPEC = Path("specs/api-designer-spec.md").read_text()
SPEC_ROUTES = {(m, p) for m, p in re.findall(r"^### \[TASK-001\] ((?:GET|POST|PUT|PATCH|DELETE)(?:, (?:GET|POST|PUT|PATCH|DELETE))*) (/\S+)", SPEC, re.M) for m in [m]}

def test_openapi_paths_match_spec_headings(app) -> None:
    spec = app.openapi()
    implemented = {(m.upper(), "/api/v1" + p) for p, ops in spec["paths"].items() for m in ops}
    documented = {(m, "/api/v1" + p) for ms, p in SPEC_ROUTES for m in ms.split(", ")}
    assert implemented == documented

def test_every_operation_has_summary_tags_and_error_responses(app) -> None: ...
def test_error_envelope_documented_for_4xx(app) -> None: ...
```

**Step 2:** Run: `uv run pytest tests/test_openapi_contract.py` Expected: FAIL until every route has `summary`, `tags`, `responses` mirroring the spec's "OpenAPI" lines. Add them to routes.

**Step 3: Implementation.** Add `summary`, `tags` (`auth`, `me`, `me-players`, `me-approvals`, `trainer`, `public`, `admin`), `response_model`, `status_code`, and `responses={...: {"model": ErrorResponse}}` to each route. Seed script inserts 10 000 users via bulk `COPY`; k6 scenarios hit `GET /me` (p95 < 100 ms), `POST /auth/login`, `GET /admin/users?query=` (p95 < 500 ms) with thresholds from Epic-01 §13. Add a README "Running locally" section (compose up, `uv run alembic upgrade head`, `uv run uvicorn app.main:create_app --factory`, `uv run arq app.jobs.worker.WorkerSettings`).

**Step 4:** Run: full QG plus `uv run pytest -m gate`. Expected: all PASS; fill `Definition of Done` evidence from `.claude/DOD.md` (Standard tier) in the chat summary, not in files.

**Step 5:** `git add tests README.md app specs/api-designer-spec.md && git commit -m "docs(api): add OpenAPI contract test, load scenarios and README"`

---

## Out of scope (do not build; see design Open Questions)

- FR-209 trainer-managed organisation users (no endpoints defined in Epic-01).
- Breached-password check (user decision: zxcvbn only).
- JWT `kid` rotation, production SMTP/SES adapter (ship the logging adapter only), Redis principal cache.
- Real implementations of `CommitmentChecker`, `PurchaseExecutor`, `CampSubmissionReader` (Epics 02/05; fakes only).

## Open points

All design decisions are resolved: see `specs/api-designer-spec.md` → "Resolved decisions"
(confirmed by user 2026-10-18). Notably dependent age is `1 <= age < 18`.

Outside sign-off only (not blocking): the default `erasure_retention_years = 7` needs legal sign-off.

## Milestones

| Milestone | Tasks | Exit check |
|---|---|---|
| M0 Foundation | 1-8 | `uv run pytest tests/core` green, app boots |
| M1 Data + contract | 9-12 | Constraint tests and `deps.py` tests green |
| M2 Auth | 13-16 | Login/refresh/register flows and 20-way gate green |
| M3 Player surface | 17-20 | `/me/*` suites and 10-way approve gate green |
| M4 Staff surface | 21-24 | Trainer/public/admin suites, erasure scan green |
| M5 Hardening | 25-28 | Matrix + cross-tenant gate, OpenAPI conformance, full QG, `/verify` |
