from datetime import date
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.identity import PlayerProfile


class PlayerProfileRepository:
    """No ``trainer_id`` here (G-10): staff access goes through ``TrainerPlayerLinkRepository``."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add(self, player: PlayerProfile) -> PlayerProfile:
        self._s.add(player)
        await self._s.flush()
        return player

    async def get(self, player_profile_id: UUID) -> PlayerProfile | None:
        return await self._s.get(PlayerProfile, player_profile_id)

    async def get_owned(self, *, owner_user_id: UUID, id: UUID) -> PlayerProfile | None:
        """The profile only when ``owner_user_id`` owns it (another owner's id is None)."""
        result = await self._s.execute(
            select(PlayerProfile).where(
                PlayerProfile.id == id, PlayerProfile.owner_user_id == owner_user_id
            )
        )
        return result.scalar_one_or_none()

    async def get_own(self, owner_user_id: UUID) -> PlayerProfile | None:
        """The account holder's own (non-dependent) profile."""
        result = await self._s.execute(
            select(PlayerProfile)
            .where(
                PlayerProfile.owner_user_id == owner_user_id,
                PlayerProfile.is_dependent.is_(False),
            )
            .order_by(PlayerProfile.created_at, PlayerProfile.id)
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def list_by_owner(self, owner_user_id: UUID) -> list[PlayerProfile]:
        result = await self._s.execute(
            select(PlayerProfile)
            .where(PlayerProfile.owner_user_id == owner_user_id)
            .order_by(PlayerProfile.created_at, PlayerProfile.id)
        )
        return list(result.scalars())

    async def get_by_login_user(self, login_user_id: UUID) -> PlayerProfile | None:
        result = await self._s.execute(
            select(PlayerProfile).where(PlayerProfile.login_user_id == login_user_id)
        )
        return result.scalar_one_or_none()

    async def find_duplicate(
        self, *, owner_user_id: UUID, display_name: str, birth_date: date
    ) -> PlayerProfile | None:
        """Same owner, same birth date and same name ignoring case."""
        result = await self._s.execute(
            select(PlayerProfile)
            .where(
                PlayerProfile.owner_user_id == owner_user_id,
                PlayerProfile.birth_date == birth_date,
                func.lower(PlayerProfile.display_name) == display_name.lower(),
            )
            .limit(1)
        )
        return result.scalar_one_or_none()
