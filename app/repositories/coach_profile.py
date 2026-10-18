from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import rowcount
from app.models.identity import CoachProfile


class CoachProfileRepository:
    """A row is one coach-at-trainer tenure (architecture 3.1)."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add(self, tenure: CoachProfile) -> CoachProfile:
        self._s.add(tenure)
        await self._s.flush()
        return tenure

    async def get_active(self, *, trainer_id: UUID, user_id: UUID) -> CoachProfile | None:
        result = await self._s.execute(
            select(CoachProfile).where(
                CoachProfile.trainer_id == trainer_id,
                CoachProfile.user_id == user_id,
                CoachProfile.is_active,
            )
        )
        return result.scalar_one_or_none()

    async def get_active_for_user(self, user_id: UUID) -> CoachProfile | None:
        """Tenant resolution for a coach principal: at most one active tenure exists."""
        result = await self._s.execute(
            select(CoachProfile).where(CoachProfile.user_id == user_id, CoachProfile.is_active)
        )
        return result.scalar_one_or_none()

    async def list_active(self, *, trainer_id: UUID) -> list[CoachProfile]:
        result = await self._s.execute(
            select(CoachProfile)
            .where(CoachProfile.trainer_id == trainer_id, CoachProfile.is_active)
            .order_by(CoachProfile.joined_at, CoachProfile.id)
        )
        return list(result.scalars())

    async def close_tenure(self, *, trainer_id: UUID, user_id: UUID, now: datetime) -> bool:
        """Set ``is_active=false`` and ``left_at`` together (``ck_coach_left_iff_inactive``)."""
        result = await self._s.execute(
            update(CoachProfile)
            .where(
                CoachProfile.trainer_id == trainer_id,
                CoachProfile.user_id == user_id,
                CoachProfile.is_active,
            )
            .values(is_active=False, left_at=now)
        )
        return rowcount(result) > 0
