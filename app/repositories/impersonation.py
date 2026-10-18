from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import rowcount
from app.models.identity import ImpersonationSession


class ImpersonationSessionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def open(
        self,
        *,
        admin_user_id: UUID,
        target_user_id: UUID,
        jti: UUID,
        expires_at: datetime,
        reason: str | None,
    ) -> ImpersonationSession:
        row = ImpersonationSession(
            admin_user_id=admin_user_id,
            target_user_id=target_user_id,
            jti=jti,
            expires_at=expires_at,
            reason=reason,
        )
        self._s.add(row)
        await self._s.flush()
        return row

    async def get_by_jti(self, jti: UUID) -> ImpersonationSession | None:
        result = await self._s.execute(
            select(ImpersonationSession).where(ImpersonationSession.jti == jti)
        )
        return result.scalar_one_or_none()

    async def close(self, jti: UUID, *, now: datetime) -> bool:
        """End an open session. False when it is unknown or already ended."""
        result = await self._s.execute(
            update(ImpersonationSession)
            .where(ImpersonationSession.jti == jti, ImpersonationSession.ended_at.is_(None))
            .values(ended_at=now)
        )
        return rowcount(result) > 0

    async def expire_due(self, *, now: datetime) -> list[UUID]:
        """End open sessions past ``expires_at``; returns their ids."""
        result = await self._s.execute(
            update(ImpersonationSession)
            .where(ImpersonationSession.ended_at.is_(None), ImpersonationSession.expires_at <= now)
            .values(ended_at=now)
            .returning(ImpersonationSession.id)
            .execution_options(synchronize_session=False)
        )
        return list(result.scalars())
