from uuid import UUID

from sqlalchemy import exists, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import new_id
from app.db.session import rowcount
from app.models.identity import LinkSource, LinkStatus, TrainerPlayerLink


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
