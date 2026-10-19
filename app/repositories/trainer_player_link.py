from collections.abc import Collection
from uuid import UUID

from sqlalchemy import exists, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.ids import new_id
from app.db.session import rowcount
from app.models.identity import (
    LinkSource,
    LinkStatus,
    PlayerProfile,
    TrainerPlayerLink,
    User,
    UserStatus,
)


class TrainerPlayerLinkRepository:
    """Tenant-scoped: every method takes a required keyword-only ``trainer_id`` (FR-022)."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, *, trainer_id: UUID, player_profile_id: UUID) -> TrainerPlayerLink | None:
        result = await self._s.execute(
            select(TrainerPlayerLink).where(
                TrainerPlayerLink.trainer_id == trainer_id,
                TrainerPlayerLink.player_profile_id == player_profile_id,
            )
        )
        return result.scalar_one_or_none()

    async def has_active_link(self, *, trainer_id: UUID, player_profile_id: UUID) -> bool:
        result = await self._s.execute(
            select(
                exists().where(
                    TrainerPlayerLink.trainer_id == trainer_id,
                    TrainerPlayerLink.player_profile_id == player_profile_id,
                    TrainerPlayerLink.status == LinkStatus.active,
                )
            )
        )
        return bool(result.scalar())

    async def has_active_link_for_owner(self, *, trainer_id: UUID, owner_user_id: UUID) -> bool:
        """The owner has an active profile actively linked to this (active) trainer."""
        trainer = aliased(User)
        result = await self._s.execute(
            select(
                exists()
                .where(
                    TrainerPlayerLink.trainer_id == trainer_id,
                    TrainerPlayerLink.status == LinkStatus.active,
                    TrainerPlayerLink.player_profile_id == PlayerProfile.id,
                    PlayerProfile.owner_user_id == owner_user_id,
                    PlayerProfile.is_active,
                    trainer.id == TrainerPlayerLink.trainer_id,
                    trainer.status == UserStatus.active,
                )
                .correlate(None)
            )
        )
        return bool(result.scalar())

    async def active_trainer_ids_for_owner(
        self, *, trainer_ids: Collection[UUID], owner_user_id: UUID
    ) -> set[UUID]:
        """The ``trainer_ids`` that ``has_active_link_for_owner`` accepts, in one query."""
        trainer = aliased(User)
        result = await self._s.execute(
            select(TrainerPlayerLink.trainer_id)
            .join(PlayerProfile, PlayerProfile.id == TrainerPlayerLink.player_profile_id)
            .join(trainer, trainer.id == TrainerPlayerLink.trainer_id)
            .where(
                TrainerPlayerLink.trainer_id.in_(trainer_ids),
                TrainerPlayerLink.status == LinkStatus.active,
                PlayerProfile.owner_user_id == owner_user_id,
                PlayerProfile.is_active,
                trainer.status == UserStatus.active,
            )
            .distinct()
        )
        return set(result.scalars())

    async def active_profile_ids_for_caller(
        self, *, trainer_id: UUID, user_id: UUID, child: bool
    ) -> frozenset[UUID]:
        """Profiles a player caller may act for under an active trainer (``player_context``).

        A child sees only the profile it logs in to; any other player sees every profile it
        owns (own plus dependents). Empty when the trainer is not active or no link is active.
        """
        trainer = aliased(User)
        caller = (
            PlayerProfile.login_user_id == user_id
            if child
            else PlayerProfile.owner_user_id == user_id
        )
        result = await self._s.execute(
            select(PlayerProfile.id)
            .join(TrainerPlayerLink, TrainerPlayerLink.player_profile_id == PlayerProfile.id)
            .join(trainer, trainer.id == TrainerPlayerLink.trainer_id)
            .where(
                TrainerPlayerLink.trainer_id == trainer_id,
                TrainerPlayerLink.status == LinkStatus.active,
                trainer.status == UserStatus.active,
                caller,
            )
        )
        return frozenset(result.scalars())

    async def list_for_trainer(
        self, *, trainer_id: UUID, status: LinkStatus = LinkStatus.active
    ) -> list[TrainerPlayerLink]:
        result = await self._s.execute(
            select(TrainerPlayerLink)
            .where(TrainerPlayerLink.trainer_id == trainer_id, TrainerPlayerLink.status == status)
            .order_by(TrainerPlayerLink.linked_at, TrainerPlayerLink.id)
        )
        return list(result.scalars())

    async def upsert_active(
        self,
        *,
        trainer_id: UUID,
        player_profile_id: UUID,
        source: LinkSource,
        share_link_id: UUID | None,
    ) -> TrainerPlayerLink:
        """Idempotent add. An inactive link is reactivated and keeps its original provenance."""
        stmt = (
            insert(TrainerPlayerLink)
            .values(
                id=new_id(),
                trainer_id=trainer_id,
                player_profile_id=player_profile_id,
                source=source,
                share_link_id=share_link_id,
            )
            .on_conflict_do_update(
                constraint="uq_trainer_player_links_trainer_player",
                set_={"status": LinkStatus.active},
            )
            .returning(TrainerPlayerLink)
        )
        result = await self._s.execute(
            select(TrainerPlayerLink).from_statement(stmt),
            execution_options={"populate_existing": True},
        )
        link: TrainerPlayerLink = result.scalar_one()
        return link

    async def activate(
        self,
        *,
        trainer_id: UUID,
        player_profile_id: UUID,
        source: LinkSource,
        share_link_id: UUID | None,
    ) -> bool:
        """Like ``upsert_active``, but reports whether anything changed.

        True when the link was inserted or reactivated; False when it was already active.
        """
        stmt = (
            insert(TrainerPlayerLink)
            .values(
                id=new_id(),
                trainer_id=trainer_id,
                player_profile_id=player_profile_id,
                source=source,
                share_link_id=share_link_id,
            )
            .on_conflict_do_update(
                constraint="uq_trainer_player_links_trainer_player",
                set_={"status": LinkStatus.active},
                where=TrainerPlayerLink.status != LinkStatus.active,
            )
            .returning(TrainerPlayerLink.id)
        )
        result = await self._s.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def deactivate(self, *, trainer_id: UUID, player_profile_id: UUID) -> bool:
        """Soft remove. Returns False when there is no active link for this tenant."""
        result = await self._s.execute(
            update(TrainerPlayerLink)
            .where(
                TrainerPlayerLink.trainer_id == trainer_id,
                TrainerPlayerLink.player_profile_id == player_profile_id,
                TrainerPlayerLink.status == LinkStatus.active,
            )
            .values(status=LinkStatus.inactive)
        )
        return rowcount(result) > 0
