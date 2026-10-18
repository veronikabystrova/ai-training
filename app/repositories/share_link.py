from datetime import datetime
from uuid import UUID

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import rowcount
from app.models.identity import ShareLink, ShareLinkKind


class ShareLinkRepository:
    """Tenant-scoped, except ``lock_by_code``: the code itself is the capability."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add(
        self,
        *,
        trainer_id: UUID,
        created_by_user_id: UUID,
        code: str,
        kind: ShareLinkKind,
        target_email: str | None = None,
        max_uses: int | None = None,
        expires_at: datetime | None = None,
    ) -> ShareLink:
        link = ShareLink(
            code=code,
            trainer_id=trainer_id,
            created_by_user_id=created_by_user_id,
            kind=kind,
            target_email=target_email,
            max_uses=max_uses,
            expires_at=expires_at,
        )
        self._s.add(link)
        await self._s.flush()
        return link

    async def get(self, share_link_id: UUID, *, trainer_id: UUID) -> ShareLink | None:
        result = await self._s.execute(
            select(ShareLink).where(
                ShareLink.id == share_link_id, ShareLink.trainer_id == trainer_id
            )
        )
        return result.scalar_one_or_none()

    async def list_for_trainer(self, *, trainer_id: UUID) -> list[ShareLink]:
        result = await self._s.execute(
            select(ShareLink)
            .where(ShareLink.trainer_id == trainer_id)
            .order_by(ShareLink.created_at, ShareLink.id)
        )
        return list(result.scalars())

    async def lock_by_code(self, code: str) -> ShareLink | None:
        """``SELECT ... FOR UPDATE``. Documented tenancy exception (the code is the capability)."""
        result = await self._s.execute(
            select(ShareLink)
            .where(ShareLink.code == code)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def increment_use(self, share_link_id: UUID, *, trainer_id: UUID) -> bool:
        """Atomic conditional increment. False when the link is spent or not in this tenant."""
        result = await self._s.execute(
            update(ShareLink)
            .where(
                ShareLink.id == share_link_id,
                ShareLink.trainer_id == trainer_id,
                or_(ShareLink.max_uses.is_(None), ShareLink.use_count < ShareLink.max_uses),
            )
            .values(use_count=ShareLink.use_count + 1)
        )
        return rowcount(result) > 0

    async def deactivate(self, share_link_id: UUID, *, trainer_id: UUID) -> bool:
        result = await self._s.execute(
            update(ShareLink)
            .where(
                ShareLink.id == share_link_id,
                ShareLink.trainer_id == trainer_id,
                ShareLink.is_active.is_(True),
            )
            .values(is_active=False)
        )
        return rowcount(result) > 0
