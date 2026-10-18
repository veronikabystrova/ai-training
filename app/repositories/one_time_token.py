from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import new_id
from app.core.security import hash_token, new_opaque_token
from app.db.session import rowcount
from app.models.identity import OneTimeToken, OttPurpose


@dataclass(frozen=True)
class ConsumedToken:
    user_id: UUID
    payload: dict[str, Any] | None


class OneTimeTokenRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def issue(
        self,
        *,
        user_id: UUID,
        purpose: OttPurpose,
        ttl: timedelta,
        now: datetime,
        payload: dict[str, Any] | None = None,
    ) -> str:
        """Create a token and supersede the user's open tokens of the same purpose."""
        await self._s.execute(
            update(OneTimeToken)
            .where(
                OneTimeToken.user_id == user_id,
                OneTimeToken.purpose == purpose,
                OneTimeToken.used_at.is_(None),
            )
            .values(used_at=now)
        )
        raw = new_opaque_token()
        self._s.add(
            OneTimeToken(
                id=new_id(),
                user_id=user_id,
                purpose=purpose,
                token_hash=hash_token(raw),
                payload=payload,
                expires_at=now + ttl,
            )
        )
        await self._s.flush()
        return raw

    async def consume(
        self, *, raw: str, purpose: OttPurpose, now: datetime
    ) -> ConsumedToken | None:
        """Atomically mark a live token used. A second call returns ``None`` (single use)."""
        row = (
            await self._s.execute(
                update(OneTimeToken)
                .where(
                    OneTimeToken.token_hash == hash_token(raw),
                    OneTimeToken.purpose == purpose,
                    OneTimeToken.used_at.is_(None),
                    OneTimeToken.expires_at > now,
                )
                .values(used_at=now)
                .returning(OneTimeToken.user_id, OneTimeToken.payload)
            )
        ).first()
        return None if row is None else ConsumedToken(user_id=row.user_id, payload=row.payload)

    async def purge(self, *, before: datetime) -> int:
        """Delete tokens that expired before ``before``; returns the number removed."""
        result = await self._s.execute(delete(OneTimeToken).where(OneTimeToken.expires_at < before))
        return rowcount(result)
