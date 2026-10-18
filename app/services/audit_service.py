from typing import Any, Protocol
from uuid import UUID

from app.repositories.audit import AuditRepository


class AuditService(Protocol):
    """Epic-07 swaps the implementation, not the call sites."""

    async def record(
        self,
        *,
        action: str,
        actor_id: UUID | None = None,
        act: UUID | None = None,
        subject_type: str | None = None,
        subject_id: UUID | None = None,
        trainer_id: UUID | None = None,
        method: str | None = None,
        path: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None: ...


class DbAuditService:
    """Stub: appends to ``audit_log`` in the caller's transaction. Never put emails in metadata."""

    def __init__(self, repository: AuditRepository) -> None:
        self._repository = repository

    async def record(
        self,
        *,
        action: str,
        actor_id: UUID | None = None,
        act: UUID | None = None,
        subject_type: str | None = None,
        subject_id: UUID | None = None,
        trainer_id: UUID | None = None,
        method: str | None = None,
        path: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        await self._repository.append(
            action=action,
            actor_user_id=actor_id,
            act_user_id=act,
            subject_type=subject_type,
            subject_id=subject_id,
            trainer_id=trainer_id,
            method=method,
            path=path,
            metadata=metadata,
        )
