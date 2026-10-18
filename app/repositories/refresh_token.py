from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import new_id
from app.core.security import hash_token, new_opaque_token
from app.db.session import rowcount
from app.models.identity import RefreshToken


class RefreshTokenRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def issue(
        self,
        *,
        user_id: UUID,
        family_id: UUID,
        expires_at: datetime,
        user_agent_hash: str | None = None,
        ip_hash: str | None = None,
    ) -> str:
        raw = new_opaque_token()
        self._s.add(
            RefreshToken(
                id=new_id(),
                user_id=user_id,
                family_id=family_id,
                token_hash=hash_token(raw),
                expires_at=expires_at,
                user_agent_hash=user_agent_hash,
                ip_hash=ip_hash,
            )
        )
        await self._s.flush()
        return raw

    async def lock_by_hash(self, token_hash: bytes) -> RefreshToken | None:
        """Row lock so concurrent refreshes with one token serialise (reuse detection)."""
        result = await self._s.execute(
            select(RefreshToken)
            .where(RefreshToken.token_hash == token_hash)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def rotate(
        self,
        old: RefreshToken,
        *,
        now: datetime,
        expires_at: datetime,
        user_agent_hash: str | None = None,
        ip_hash: str | None = None,
    ) -> str:
        """Mark ``old`` consumed and issue its successor in the same family."""
        old.consumed_at = now
        return await self.issue(
            user_id=old.user_id,
            family_id=old.family_id,
            expires_at=expires_at,
            user_agent_hash=user_agent_hash,
            ip_hash=ip_hash,
        )

    async def revoke_family(self, family_id: UUID, *, now: datetime) -> int:
        result = await self._s.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        return rowcount(result)

    async def purge(self, *, before: datetime) -> int:
        result = await self._s.execute(delete(RefreshToken).where(RefreshToken.expires_at < before))
        return rowcount(result)
