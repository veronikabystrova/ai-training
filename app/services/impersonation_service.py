"""Impersonation lifecycle. Start is Task 24 (admin); exit lives here.

A session's refresh family is linked by convention: ``refresh_tokens.family_id`` equals
``impersonation_sessions.jti`` (architecture section 5), so ending a session revokes exactly
its own family and never the target's regular sessions.
"""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import Clock
from app.core.errors import ForbiddenError
from app.repositories.impersonation import ImpersonationSessionRepository
from app.repositories.refresh_token import RefreshTokenRepository


class ImpersonationService:
    def __init__(self, *, session: AsyncSession, clock: Clock) -> None:
        self._session = session
        self._clock = clock
        self._impersonations = ImpersonationSessionRepository(session)
        self._refresh_tokens = RefreshTokenRepository(session)

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
