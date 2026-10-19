from collections.abc import Collection
from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from sqlalchemy import ColumnElement, Select, false, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.identity import (
    LinkSource,
    LinkStatus,
    PlayerProfile,
    TrainerPlayerLink,
    TrainerProfile,
    User,
    UserStatus,
)


@dataclass(frozen=True, slots=True)
class PlayerRow:
    profile: PlayerProfile
    has_login: bool  # ``login_user_id`` is set and that user is active


@dataclass(frozen=True, slots=True)
class ProfileTrainerLink:
    player_profile_id: UUID
    trainer_id: UUID
    business_name: str
    trainer_active: bool
    logo_key: str | None
    status: LinkStatus
    source: LinkSource
    linked_at: datetime


class PlayerProfileRepository:
    """No ``trainer_id`` here (G-10): staff access goes through ``TrainerPlayerLinkRepository``.

    Removed dependents (``is_active`` false) are invisible to every owner-scoped read.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add(self, player: PlayerProfile) -> PlayerProfile:
        self._s.add(player)
        await self._s.flush()
        return player

    async def get(self, player_profile_id: UUID) -> PlayerProfile | None:
        return await self._s.get(PlayerProfile, player_profile_id)

    async def get_owned(self, *, owner_user_id: UUID, id: UUID) -> PlayerProfile | None:
        """The active profile only when ``owner_user_id`` owns it (another owner's id is None)."""
        result = await self._s.execute(
            select(PlayerProfile).where(
                PlayerProfile.id == id,
                PlayerProfile.owner_user_id == owner_user_id,
                PlayerProfile.is_active,
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
            .where(PlayerProfile.owner_user_id == owner_user_id, PlayerProfile.is_active)
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
                PlayerProfile.is_active,
                PlayerProfile.birth_date == birth_date,
                func.lower(PlayerProfile.display_name) == display_name.lower(),
            )
            .limit(1)
        )
        return result.scalar_one_or_none()

    # --- views for the parent / child API --------------------------------------------------

    def _views(self, *where: ColumnElement[bool]) -> Select[PlayerProfile, bool]:
        login = User.status == UserStatus.active
        return (
            select(PlayerProfile, func.coalesce(login, false()).label("has_login"))
            .outerjoin(User, User.id == PlayerProfile.login_user_id)
            .where(PlayerProfile.is_active, *where)
            # the account holder's own profile first, then dependents
            .order_by(PlayerProfile.is_dependent, PlayerProfile.created_at, PlayerProfile.id)
        )

    async def list_views_for_owner(
        self, *, owner_user_id: UUID, player_profile_id: UUID | None = None
    ) -> list[PlayerRow]:
        stmt = self._views(PlayerProfile.owner_user_id == owner_user_id)
        if player_profile_id is not None:
            stmt = stmt.where(PlayerProfile.id == player_profile_id)
        rows = (await self._s.execute(stmt)).all()
        return [PlayerRow(profile, bool(has_login)) for profile, has_login in rows]

    async def list_views_for_login(
        self, *, login_user_id: UUID, player_profile_id: UUID | None = None
    ) -> list[PlayerRow]:
        """A child login sees only the profile it signs in to."""
        stmt = self._views(PlayerProfile.login_user_id == login_user_id)
        if player_profile_id is not None:
            stmt = stmt.where(PlayerProfile.id == player_profile_id)
        rows = (await self._s.execute(stmt)).all()
        return [PlayerRow(profile, bool(has_login)) for profile, has_login in rows]

    async def list_trainer_links(
        self, *, owner_user_id: UUID, player_profile_ids: Collection[UUID] | None = None
    ) -> list[ProfileTrainerLink]:
        """Links of the owner's active profiles (any status), with each trainer's display data."""
        stmt = (
            select(
                TrainerPlayerLink.player_profile_id,
                TrainerPlayerLink.trainer_id,
                TrainerProfile.business_name,
                User.status == UserStatus.active,
                TrainerProfile.logo_key,
                TrainerPlayerLink.status,
                TrainerPlayerLink.source,
                TrainerPlayerLink.linked_at,
            )
            .join(PlayerProfile, PlayerProfile.id == TrainerPlayerLink.player_profile_id)
            .join(TrainerProfile, TrainerProfile.user_id == TrainerPlayerLink.trainer_id)
            .join(User, User.id == TrainerPlayerLink.trainer_id)
            .where(PlayerProfile.owner_user_id == owner_user_id, PlayerProfile.is_active)
            .order_by(TrainerPlayerLink.linked_at, TrainerPlayerLink.id)
        )
        if player_profile_ids is not None:
            stmt = stmt.where(TrainerPlayerLink.player_profile_id.in_(player_profile_ids))
        return [ProfileTrainerLink(*row) for row in (await self._s.execute(stmt)).all()]
