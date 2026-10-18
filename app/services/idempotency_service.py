from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from app.core.errors import ConflictError
from app.repositories.idempotency import IdempotencyRepository


@dataclass(frozen=True, slots=True)
class StoredResponse:
    status_code: int
    body: dict[str, Any] | None


class IdempotencyGuard:
    """One request's idempotency claim, handed to the service by ``IdempotencyDep``.

    The service calls ``claim()`` first and ``store()`` last, both inside the transaction that
    makes the decision, so a rollback stores nothing and a concurrent duplicate waits on the
    unique index until the first request commits (spec "Conventions: Idempotency").
    """

    def __init__(
        self,
        repository: IdempotencyRepository,
        *,
        key: str,
        principal_id: UUID,
        endpoint: str,
        request_hash: str,
        expires_at: datetime,
    ) -> None:
        self._repository = repository
        self._key = key
        self._principal_id = principal_id
        self._endpoint = endpoint
        self._request_hash = request_hash
        self._expires_at = expires_at

    async def claim(self) -> StoredResponse | None:
        """``None`` when this call owns the key; the stored response when it is a replay."""
        created = await self._repository.reserve(
            key=self._key,
            principal_id=self._principal_id,
            endpoint=self._endpoint,
            request_hash=self._request_hash,
            expires_at=self._expires_at,
        )
        if created:
            return None
        row = await self._repository.get(key=self._key, principal_id=self._principal_id)
        # A row without a response means the owner never called store(): refuse to guess.
        if row is None or row.request_hash != self._request_hash or row.response_status is None:
            raise ConflictError(
                "IDEMPOTENCY_KEY_REUSED", "Idempotency key already used for a different request"
            )
        return StoredResponse(row.response_status, row.response_body)

    async def store(self, status_code: int, body: dict[str, Any] | None) -> None:
        await self._repository.store_response(
            key=self._key,
            principal_id=self._principal_id,
            response_status=status_code,
            response_body=body,
        )
