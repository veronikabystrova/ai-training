from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import new_id
from app.db.session import rowcount
from app.models.platform import IdempotencyKey


class IdempotencyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, *, key: str, principal_id: UUID) -> IdempotencyKey | None:
        result = await self._s.execute(
            select(IdempotencyKey)
            .where(IdempotencyKey.key == key, IdempotencyKey.principal_id == principal_id)
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def reserve(
        self,
        *,
        key: str,
        principal_id: UUID,
        endpoint: str,
        request_hash: str,
        expires_at: datetime,
    ) -> bool:
        """Claim the key. True if this call created it, False if it already existed."""
        result = await self._s.execute(
            insert(IdempotencyKey)
            .values(
                id=new_id(),
                key=key,
                principal_id=principal_id,
                endpoint=endpoint,
                request_hash=request_hash,
                expires_at=expires_at,
            )
            .on_conflict_do_nothing(constraint="uq_idempotency_keys_key_principal_id")
            .returning(IdempotencyKey.id)
        )
        return result.first() is not None

    async def store_response(
        self,
        *,
        key: str,
        principal_id: UUID,
        response_status: int,
        response_body: dict[str, Any] | None,
    ) -> None:
        await self._s.execute(
            update(IdempotencyKey)
            .where(IdempotencyKey.key == key, IdempotencyKey.principal_id == principal_id)
            .values(response_status=response_status, response_body=response_body)
            .execution_options(synchronize_session=False)
        )

    async def purge(self, *, before: datetime) -> int:
        result = await self._s.execute(
            delete(IdempotencyKey).where(IdempotencyKey.expires_at < before)
        )
        return rowcount(result)
