from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.identity import ShareLinkEvent, ShareLinkEventKind


class ShareLinkEventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def append(
        self,
        *,
        share_link_id: UUID,
        kind: ShareLinkEventKind,
        user_agent_hash: str | None = None,
        ip_hash: str | None = None,
    ) -> ShareLinkEvent:
        """``kind`` is ``opened`` or ``registered`` (``ck_share_link_events_kind``)."""
        event = ShareLinkEvent(
            share_link_id=share_link_id,
            kind=kind,
            user_agent_hash=user_agent_hash,
            ip_hash=ip_hash,
        )
        self._s.add(event)
        await self._s.flush()
        return event
