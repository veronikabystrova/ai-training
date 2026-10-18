from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.identity import UserErasure


class UserErasureRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def append(
        self,
        *,
        original_user_id: UUID,
        original_email_hash: str,
        erased_by_user_id: UUID,
        reason: str | None,
        legal_retention_until: datetime,
    ) -> UserErasure:
        row = UserErasure(
            original_user_id=original_user_id,
            original_email_hash=original_email_hash,
            erased_by_user_id=erased_by_user_id,
            reason=reason,
            legal_retention_until=legal_retention_until,
        )
        self._s.add(row)
        await self._s.flush()
        return row
