from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.identity import Profile, TrainerProfile, User, UserStatus


class ProfileRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add(self, profile: Profile) -> Profile:
        self._s.add(profile)
        await self._s.flush()
        return profile

    async def get(self, user_id: UUID) -> Profile | None:
        return await self._s.get(Profile, user_id)


class TrainerProfileRepository:
    """``trainer_profiles.user_id`` is the tenant id, so this table is the tenant root."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add(self, profile: TrainerProfile) -> TrainerProfile:
        self._s.add(profile)
        await self._s.flush()
        return profile

    async def get(self, user_id: UUID) -> TrainerProfile | None:
        return await self._s.get(TrainerProfile, user_id)

    async def get_by_slug(self, slug: str) -> TrainerProfile | None:
        result = await self._s.execute(select(TrainerProfile).where(TrainerProfile.slug == slug))
        return result.scalar_one_or_none()

    async def get_active_by_slug(self, slug: str) -> TrainerProfile | None:
        """Public lookup: only a trainer whose account is active (G-15: deactivated is hidden)."""
        result = await self._s.execute(
            select(TrainerProfile)
            .join(User, User.id == TrainerProfile.user_id)
            .where(TrainerProfile.slug == slug, User.status == UserStatus.active)
        )
        return result.scalar_one_or_none()
