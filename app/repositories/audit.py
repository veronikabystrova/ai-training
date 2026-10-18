from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.platform import AuditLog


class AuditRepository:
    """Minimal append-only stub; Epic-07 extends it. Never pass emails in ``metadata``."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def append(
        self,
        *,
        action: str,
        actor_user_id: UUID | None = None,
        act_user_id: UUID | None = None,
        subject_type: str | None = None,
        subject_id: UUID | None = None,
        trainer_id: UUID | None = None,
        method: str | None = None,
        path: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AuditLog:
        row = AuditLog(
            action=action,
            actor_user_id=actor_user_id,
            act_user_id=act_user_id,
            subject_type=subject_type,
            subject_id=subject_id,
            trainer_id=trainer_id,
            method=method,
            path=path,
            metadata_=metadata or {},
        )
        self._s.add(row)
        await self._s.flush()
        return row
