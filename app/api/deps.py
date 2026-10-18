"""The principal / tenant contract (architecture section 2), consumed by every epic.

Epics obtain identity and tenant only through these dependencies. Changing a signature here
is a breaking change for every epic.

Transactions: dependencies never leave a transaction open. Each read ends with ``commit()``
(a read-only commit is a no-op for the database) so a service can open its own transaction
afterwards. Idempotent endpoints claim their key inside the service transaction instead
(``IdempotencyGuard``).
"""

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, Header, Query, Request
from sqlalchemy import and_, false, select, true
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.clock import Clock
from app.core.config import Settings
from app.core.errors import (
    BadRequestError,
    ForbiddenError,
    NotFoundError,
    UnauthorizedError,
    UnprocessableError,
)
from app.core.idempotency import request_hash
from app.core.ratelimit import RateLimiter, RouteClass
from app.core.security import TokenIssuer
from app.db.session import SessionFactory
from app.models.identity import (
    CoachProfile,
    ImpersonationSession,
    User,
    UserRole,
    UserStatus,
)
from app.repositories.idempotency import IdempotencyRepository
from app.repositories.trainer_player_link import TrainerPlayerLinkRepository
from app.services.idempotency_service import IdempotencyGuard

Role = UserRole

_IDEMPOTENCY_KEY_MAX = 64


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: UUID
    role: Role
    trainer_id: UUID | None  # trainer: self; coach: active tenure's trainer; else None
    act: UUID | None  # impersonating super_admin's user_id, else None
    child: bool  # True iff a dependent player_profiles.login_user_id = user_id
    jti: UUID


@dataclass(frozen=True, slots=True)
class TenantScope:
    trainer_id: UUID  # the only tenant id services may use
    principal: Principal
    # Player callers: profiles they may act for under this trainer (own plus dependents, or
    # only the child's own profile). Empty for staff.
    player_profile_ids: frozenset[UUID]


# --- providers (state is set by ``create_app``; tests build the same state) ----------------


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """One ``AsyncSession`` per request. Services open the transaction; we only close."""
    factory: SessionFactory = request.app.state.session_factory
    async with factory() as session:
        yield session


def get_clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


def get_issuer(request: Request) -> TokenIssuer:
    issuer: TokenIssuer = request.app.state.token_issuer
    return issuer


def get_settings_dep(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_rate_limiter(request: Request) -> RateLimiter:
    limiter: RateLimiter = request.app.state.rate_limiter
    return limiter


SessionDep = Annotated[AsyncSession, Depends(get_session)]
ClockDep = Annotated[Clock, Depends(get_clock)]
IssuerDep = Annotated[TokenIssuer, Depends(get_issuer)]
SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
RateLimiterDep = Annotated[RateLimiter, Depends(get_rate_limiter)]


# --- authentication ------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _PrincipalRow:
    status: UserStatus
    role: Role
    coach_trainer_id: UUID | None
    tenant_status: UserStatus | None
    session_found: bool
    session_ended: bool
    session_expires_at: datetime | None


async def _resolve(
    session: AsyncSession, *, user_id: UUID, jti: UUID, impersonating: bool
) -> _PrincipalRow | None:
    """The single revalidation statement (A-03): indexed lookups only.

    ``cp.is_active`` stays a bare column: ``IS true`` would disable the partial indexes
    ``uq_coach_one_active_trainer`` / ``ix_coach_trainer_active``. ``jti`` is bound as a UUID.
    """
    u = aliased(User)
    cp = aliased(CoachProfile)
    tenant = aliased(User)
    imp = aliased(ImpersonationSession)
    stmt = (
        select(
            u.status,
            u.role,
            cp.trainer_id.label("coach_trainer_id"),
            tenant.status.label("tenant_status"),
            imp.id.label("session_id"),
            imp.ended_at.label("session_ended_at"),
            imp.expires_at.label("session_expires_at"),
        )
        .select_from(u)
        .outerjoin(cp, and_(cp.user_id == u.id, cp.is_active))
        .outerjoin(tenant, tenant.id == cp.trainer_id)
        .outerjoin(
            imp,
            and_(
                imp.jti == jti,
                imp.target_user_id == u.id,
                true() if impersonating else false(),
            ),
        )
        .where(u.id == user_id)
    )
    row = (await session.execute(stmt)).one_or_none()
    if row is None:
        return None
    return _PrincipalRow(
        status=row.status,
        role=row.role,
        coach_trainer_id=row.coach_trainer_id,
        tenant_status=row.tenant_status,
        session_found=row.session_id is not None,
        session_ended=row.session_ended_at is not None,
        session_expires_at=row.session_expires_at,
    )


def _bearer_token(request: Request) -> str | None:
    header = request.headers.get("Authorization")
    if header is None:
        return None
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise UnauthorizedError()
    return token.strip()


def _uuid_claim(claims: dict[str, Any], name: str) -> UUID | None:
    value = claims.get(name)
    if value is None:
        return None
    try:
        return UUID(str(value))
    except ValueError as exc:
        raise UnauthorizedError() from exc


async def _authenticate(
    request: Request, session: AsyncSession, issuer: TokenIssuer, clock: Clock, token: str
) -> Principal:
    claims = issuer.decode(token)
    user_id = _uuid_claim(claims, "sub")
    jti = _uuid_claim(claims, "jti")
    if user_id is None or jti is None:
        raise UnauthorizedError()
    act = _uuid_claim(claims, "act")
    token_tid = _uuid_claim(claims, "tid")

    row = await _resolve(session, user_id=user_id, jti=jti, impersonating=act is not None)
    await session.commit()  # end the read transaction (see module docstring)

    if row is None or row.status is not UserStatus.active:
        raise UnauthorizedError()

    trainer_id: UUID | None = None
    if row.role is Role.trainer:
        trainer_id = user_id
    elif row.role is Role.coach:
        # DB is authoritative: a moved coach holds a stale token and must refresh.
        if row.coach_trainer_id is None or token_tid != row.coach_trainer_id:
            raise UnauthorizedError()
        trainer_id = row.coach_trainer_id

    if act is not None and (
        not row.session_found
        or row.session_ended
        or row.session_expires_at is None
        or row.session_expires_at <= clock.now()
    ):
        raise UnauthorizedError("IMPERSONATION_EXPIRED", "Impersonation session expired")

    if row.role is Role.coach and row.tenant_status is not UserStatus.active:
        raise NotFoundError()  # G-15: deactivated tenant is invisible to its coaches

    principal = Principal(
        user_id=user_id,
        role=row.role,
        trainer_id=trainer_id,
        act=act,
        child=bool(claims.get("child", False)),
        jti=jti,
    )
    request.state.principal = principal  # read by the impersonation middleware
    return principal


async def current_principal(
    request: Request, session: SessionDep, issuer: IssuerDep, clock: ClockDep
) -> Principal:
    # No must_change_password gate: pending trainers cannot log in, and password/setup
    # activates and clears the flag atomically (password/change dropped 2026-10-18).
    token = _bearer_token(request)
    if token is None:
        raise UnauthorizedError()
    return await _authenticate(request, session, issuer, clock, token)


async def optional_principal(
    request: Request, session: SessionDep, issuer: IssuerDep, clock: ClockDep
) -> Principal | None:
    token = _bearer_token(request)
    if token is None:
        return None
    return await _authenticate(request, session, issuer, clock, token)


PrincipalDep = Annotated[Principal, Depends(current_principal)]
OptionalPrincipalDep = Annotated[Principal | None, Depends(optional_principal)]


# --- authorisation -------------------------------------------------------------------------


def require_role(*roles: Role) -> Callable[[Principal], Principal]:
    """Role gate only, no tenant logic. Use as ``Depends(require_role(Role.trainer))``."""
    allowed = frozenset(roles)

    def _require(principal: PrincipalDep) -> Principal:
        if principal.role not in allowed:
            raise ForbiddenError()
        return principal

    return _require


def require_super_admin(principal: PrincipalDep) -> Principal:
    # An impersonating admin carries the target's role, but reject ``act`` explicitly (US-01.07).
    if principal.act is not None or principal.role is not Role.super_admin:
        raise ForbiddenError()
    return principal


SuperAdminScope = Annotated[Principal, Depends(require_super_admin)]


async def _player_scope(
    session: AsyncSession, principal: Principal, trainer_id: UUID | None
) -> TenantScope:
    # Every miss is a 404, never 403: no tenant oracle.
    if principal.role is not Role.player or trainer_id is None:
        raise NotFoundError()
    ids = await TrainerPlayerLinkRepository(session).active_profile_ids_for_caller(
        trainer_id=trainer_id, user_id=principal.user_id, child=principal.child
    )
    await session.commit()  # end the read transaction (see module docstring)
    if not ids:
        raise NotFoundError()
    return TenantScope(trainer_id=trainer_id, principal=principal, player_profile_ids=ids)


async def player_context(
    principal: PrincipalDep, session: SessionDep, trainer_id: Annotated[UUID, Query()]
) -> TenantScope:
    return await _player_scope(session, principal, trainer_id)


async def require_tenant_scope(
    principal: PrincipalDep,
    session: SessionDep,
    trainer_id: Annotated[UUID | None, Query()] = None,
) -> TenantScope:
    """Staff get their own tenant (a ``trainer_id`` query is ignored); players need a context."""
    if principal.role in (Role.trainer, Role.coach) and principal.trainer_id is not None:
        return TenantScope(
            trainer_id=principal.trainer_id, principal=principal, player_profile_ids=frozenset()
        )
    if principal.role is Role.player:
        return await _player_scope(session, principal, trainer_id)
    raise ForbiddenError()  # super_admin must use SuperAdminScope


TenantDep = Annotated[TenantScope, Depends(require_tenant_scope)]
PlayerContextDep = Annotated[TenantScope, Depends(player_context)]


# --- idempotency and rate limiting ---------------------------------------------------------


async def idempotency(
    request: Request,
    principal: PrincipalDep,
    session: SessionDep,
    clock: ClockDep,
    settings: SettingsDep,
    idempotency_key: Annotated[str | None, Header()] = None,
) -> IdempotencyGuard:
    """Validates ``Idempotency-Key`` and builds the guard. Touches no database."""
    key = (idempotency_key or "").strip()
    if not key:
        raise BadRequestError("IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key header is required")
    if len(key) > _IDEMPOTENCY_KEY_MAX:
        raise UnprocessableError(
            details=[{"field": "Idempotency-Key", "message": "must be at most 64 characters"}]
        )
    raw = await request.body()
    body: object = None
    if raw:
        try:
            body = json.loads(raw)
        except ValueError:
            body = raw.decode(errors="replace")
    return IdempotencyGuard(
        IdempotencyRepository(session),
        key=key,
        principal_id=principal.user_id,
        endpoint=request.url.path[:255],
        request_hash=request_hash(request.method, request.url.path, body),
        expires_at=clock.now() + timedelta(hours=settings.idempotency_ttl_hours),
    )


IdempotencyDep = Annotated[IdempotencyGuard, Depends(idempotency)]

_IP_CLASSES = frozenset({RouteClass.login_ip, RouteClass.register_ip, RouteClass.public_preview_ip})
_PRINCIPAL_CLASSES = frozenset({RouteClass.verify_resend_user, RouteClass.default_principal})


def rate_limit(route_class: RouteClass) -> Callable[..., Awaitable[None]]:
    """Dependency factory. IP classes key on the client address, principal classes on the user.

    Email-keyed classes are counted by the service, which has the validated email; asking for
    one here is a programming error. A Redis outage fails open (see ``RateLimiter.hit``).
    """
    if route_class in _IP_CLASSES:

        async def _by_ip(request: Request, limiter: RateLimiterDep) -> None:
            await limiter.hit(route_class, request.client.host if request.client else "unknown")

        return _by_ip
    if route_class in _PRINCIPAL_CLASSES:

        async def _by_principal(principal: PrincipalDep, limiter: RateLimiterDep) -> None:
            await limiter.hit(route_class, str(principal.user_id))

        return _by_principal
    raise ValueError(f"{route_class.name} is keyed by email and is counted in the service")
