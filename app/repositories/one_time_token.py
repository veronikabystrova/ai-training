from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import new_id
from app.core.security import hash_token, new_opaque_token
from app.db.session import rowcount
from app.models.identity import OneTimeToken, OttPurpose, Profile, User


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

    async def peek_user_inputs(self, *, raw: str, purpose: OttPurpose, now: datetime) -> list[str]:
        """Read-only: email and names of a live token's user (password-strength context).

        Empty when the token is unknown, used or expired. Nothing is consumed or locked.
        """
        row = (
            await self._s.execute(
                select(User.email, Profile.first_name, Profile.last_name)
                .select_from(OneTimeToken)
                .join(User, User.id == OneTimeToken.user_id)
                .outerjoin(Profile, Profile.user_id == User.id)
                .where(
                    OneTimeToken.token_hash == hash_token(raw),
                    OneTimeToken.purpose == purpose,
                    OneTimeToken.used_at.is_(None),
                    OneTimeToken.expires_at > now,
                )
            )
        ).first()
        return [] if row is None else [value for value in row if value]

    async def live_owner(self, *, raw: str, purpose: OttPurpose, now: datetime) -> UUID | None:
        """Read-only: owner of a token that is still unused and unexpired, else ``None``."""
        return (
            await self._s.execute(
                select(OneTimeToken.user_id).where(
                    OneTimeToken.token_hash == hash_token(raw),
                    OneTimeToken.purpose == purpose,
                    OneTimeToken.used_at.is_(None),
                    OneTimeToken.expires_at > now,
                )
            )
        ).scalar_one_or_none()

    async def purge(self, *, before: datetime, used_before: datetime | None = None) -> int:
        """Delete tokens that expired before ``before`` or were used before ``used_before``."""
        stale = OneTimeToken.expires_at < before
        if used_before is not None:
            stale = or_(stale, OneTimeToken.used_at < used_before)
        result = await self._s.execute(delete(OneTimeToken).where(stale))
        return rowcount(result)
