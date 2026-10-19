"""Impersonation lifecycle: start (admin) and exit.

A session's refresh family is linked by convention: ``refresh_tokens.family_id`` equals
``impersonation_sessions.jti`` (architecture section 5), so ending a session revokes exactly
its own family and never the target's regular sessions, and the refresh path recognises the
family as an impersonation and keeps ``act``.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import Clock
from app.core.config import Settings
from app.core.errors import ConflictError, ForbiddenError, NotFoundError
from app.core.ids import new_id
from app.core.security import ClientFingerprint, TokenIssuer
from app.models.identity import User, UserRole, UserStatus
from app.repositories.coach_profile import CoachProfileRepository
from app.repositories.impersonation import ImpersonationSessionRepository
from app.repositories.player import PlayerProfileRepository
from app.repositories.refresh_token import RefreshTokenRepository
from app.repositories.user import UserRepository
from app.services.audit_service import AuditService


@dataclass(frozen=True, slots=True)
class StartedImpersonation:
    access_token: str
    refresh_token: str
    expires_at: datetime
    target: User
    trainer_id: UUID | None
    child: bool


class ImpersonationService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        clock: Clock,
        settings: Settings,
        issuer: TokenIssuer,
        audit: AuditService,
    ) -> None:
        self._session = session
        self._clock = clock
        self._settings = settings
        self._issuer = issuer
        self._audit = audit
        self._users = UserRepository(session)
        self._coaches = CoachProfileRepository(session)
        self._players = PlayerProfileRepository(session)
        self._impersonations = ImpersonationSessionRepository(session)
        self._refresh_tokens = RefreshTokenRepository(session)

    async def start(
        self, *, admin_id: UUID, target_id: UUID, client: ClientFingerprint
    ) -> StartedImpersonation:
        """Open a session for an active, non-admin target and issue its token pair.

        The access token carries ``sub=target, act=admin`` and the session's ``jti``; the refresh
        family id is that same ``jti`` (see the module docstring). Both die with the session.
        """
        target = await self._users.get(target_id)
        if target is None:
            raise NotFoundError()
        if target.role is UserRole.super_admin:
            raise ForbiddenError("CANNOT_IMPERSONATE_ADMIN", "An admin cannot be impersonated")
        if target.status is not UserStatus.active:
            raise ConflictError("USER_NOT_ACTIVE", "The target user is not active")

        now = self._clock.now()
        lifetime = timedelta(minutes=self._settings.impersonation_max_minutes)
        jti = new_id()
        session = await self._impersonations.open(
            admin_user_id=admin_id,
            target_user_id=target.id,
            jti=jti,
            expires_at=now + lifetime,
            reason=None,
        )
        trainer_id = await self._tenant_of(target)
        child = await self._is_child(target)
        access, _ = self._issuer.issue_access(
            user_id=target.id,
            role=target.role.value,
            trainer_id=trainer_id,
            act=admin_id,
            child=child,
            ttl=int(lifetime.total_seconds()),
            jti=jti,
        )
        refresh = await self._refresh_tokens.issue(
            user_id=target.id,
            family_id=jti,
            expires_at=session.expires_at,
            user_agent_hash=client.user_agent_hash,
            ip_hash=client.ip_hash,
        )
        await self._audit.record(
            action="impersonation.started",
            actor_id=admin_id,
            subject_type="user",
            subject_id=target.id,
            trainer_id=trainer_id,
        )
        await self._session.commit()
        return StartedImpersonation(
            access_token=access,
            refresh_token=refresh,
            expires_at=session.expires_at,
            target=target,
            trainer_id=trainer_id,
            child=child,
        )

    async def _tenant_of(self, user: User) -> UUID | None:
        if user.role is UserRole.trainer:
            return user.id
        if user.role is UserRole.coach:
            tenure = await self._coaches.get_active_for_user(user.id)
            return tenure.trainer_id if tenure else None
        return None

    async def _is_child(self, user: User) -> bool:
        if user.role is not UserRole.player:
            return False
        profile = await self._players.get_by_login_user(user.id)
        return profile is not None and profile.is_dependent

    async def exit(self, *, act: UUID | None, session_jti: UUID) -> None:
        """End the caller's impersonation session and revoke its refresh family.

        ``act`` is the principal's ``act`` claim: without it the caller is not impersonating.
        Authentication already rejected ended or expired sessions (401 IMPERSONATION_EXPIRED).
        """
        if act is None:
            raise ForbiddenError()
        now = self._clock.now()
        await self._impersonations.close(session_jti, now=now)
        await self._refresh_tokens.revoke_family(session_jti, now=now)
        await self._session.commit()
