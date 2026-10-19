"""Scheduled sweeps: approval and impersonation expiry, and the nightly purge.

Each sweep is one transaction. Every statement carries its own status guard, so overlapping
runs and re-runs do nothing twice.
"""

from dataclasses import dataclass
from datetime import timedelta

import structlog

from app.core.clock import Clock
from app.db.session import SessionFactory
from app.repositories.approval import PurchaseApprovalRepository
from app.repositories.idempotency import IdempotencyRepository
from app.repositories.impersonation import ImpersonationSessionRepository
from app.repositories.one_time_token import OneTimeTokenRepository
from app.repositories.refresh_token import RefreshTokenRepository

USED_TOKEN_RETENTION = timedelta(days=7)


@dataclass(frozen=True, slots=True)
class PurgeResult:
    one_time_tokens: int
    refresh_tokens: int
    idempotency_keys: int


class MaintenanceService:
    def __init__(self, *, session_factory: SessionFactory, clock: Clock) -> None:
        self._session_factory = session_factory
        self._clock = clock
        self._log = structlog.get_logger(__name__)

    async def expire_purchase_approvals(self) -> int:
        async with self._session_factory() as session:
            ids = await PurchaseApprovalRepository(session).expire_due(now=self._clock.now())
            await session.commit()
        self._log.info("approvals.expired", count=len(ids))
        return len(ids)

    async def expire_impersonation_sessions(self) -> int:
        async with self._session_factory() as session:
            ids = await ImpersonationSessionRepository(session).expire_due(now=self._clock.now())
            await session.commit()
        self._log.info("impersonation_sessions.expired", count=len(ids))
        return len(ids)

    async def purge_expired_tokens(self) -> PurgeResult:
        """One-time tokens expired or used over 7 days ago; expired refresh and idempotency rows."""
        now = self._clock.now()
        async with self._session_factory() as session:
            result = PurgeResult(
                one_time_tokens=await OneTimeTokenRepository(session).purge(
                    before=now, used_before=now - USED_TOKEN_RETENTION
                ),
                refresh_tokens=await RefreshTokenRepository(session).purge(before=now),
                idempotency_keys=await IdempotencyRepository(session).purge(before=now),
            )
            await session.commit()
        self._log.info(
            "tokens.purged",
            one_time=result.one_time_tokens,
            refresh=result.refresh_tokens,
            idempotency=result.idempotency_keys,
        )
        return result
