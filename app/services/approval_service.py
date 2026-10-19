"""A parent's purchase approvals: list, approve, deny.

The decision runs in one transaction: idempotency claim, row lock, executor call, status
change and stored response commit together, so a failure leaves nothing behind and ten
parallel approvals produce exactly one effect.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import Clock
from app.core.errors import ConflictError, NotFoundError
from app.core.jobs import JobQueue, enqueue_best_effort
from app.models.identity import ApprovalStatus, PurchaseApproval
from app.repositories.approval import PurchaseApprovalRepository
from app.services.idempotency_service import IdempotencyGuard
from app.services.ports import PurchaseExecutor

DENIED_JOB = "notify_child_approval_denied"


class Decision(StrEnum):
    approve = "approve"
    deny = "deny"


@dataclass(frozen=True, slots=True)
class ApprovalView:
    id: UUID
    trainer_id: UUID
    player_profile_id: UUID
    amount: Decimal
    currency: Literal["USD", "TOKENS"]
    description: str
    status: ApprovalStatus
    created_at: datetime
    expires_at: datetime
    decided_at: datetime | None
    decision_note: str | None

    def to_json(self) -> dict[str, Any]:
        """JSON-safe form stored for idempotent replay (parsed back by the response schema)."""
        return {
            "id": str(self.id),
            "trainer_id": str(self.trainer_id),
            "player_profile_id": str(self.player_profile_id),
            "amount": str(self.amount),
            "currency": self.currency,
            "description": self.description,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "decided_at": self.decided_at.isoformat() if self.decided_at else None,
            "decision_note": self.decision_note,
        }


@dataclass(frozen=True, slots=True)
class DecisionResult:
    """``body`` is the stored JSON; ``replayed`` is true when it came from an earlier call."""

    status_code: int
    body: dict[str, Any]
    replayed: bool


def approval_view(approval: PurchaseApproval) -> ApprovalView:
    currency: Literal["USD", "TOKENS"]
    if approval.amount_cents is not None:
        amount = (Decimal(approval.amount_cents) / 100).quantize(Decimal("0.01"))
        currency = "USD"
    else:
        amount = Decimal(approval.token_amount or 0)
        currency = "TOKENS"
    return ApprovalView(
        id=approval.id,
        trainer_id=approval.trainer_id,
        player_profile_id=approval.player_profile_id,
        amount=amount,
        currency=currency,
        description=approval.subject_type,
        status=approval.status,
        created_at=approval.requested_at,
        expires_at=approval.expires_at,
        decided_at=approval.responded_at,
        decision_note=approval.parent_note,
    )


class ApprovalService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        clock: Clock,
        executor: PurchaseExecutor,
        jobs: JobQueue,
    ) -> None:
        self._session = session
        self._clock = clock
        self._executor = executor
        self._jobs = jobs
        self._approvals = PurchaseApprovalRepository(session)

    async def list_approvals(
        self,
        *,
        parent_user_id: UUID,
        trainer_id: UUID,
        status: ApprovalStatus | None,
        limit: int,
        cursor: str | None,
    ) -> tuple[list[ApprovalView], str | None]:
        rows, next_cursor = await self._approvals.list_for_parent(
            parent_user_id=parent_user_id,
            trainer_id=trainer_id,
            status=status,
            limit=limit,
            cursor=cursor,
        )
        await self._session.commit()  # end the read transaction
        return [approval_view(r) for r in rows], next_cursor

    async def decide(
        self,
        *,
        parent_user_id: UUID,
        approval_id: UUID,
        decision: Decision,
        note: str | None,
        guard: IdempotencyGuard,
    ) -> DecisionResult:
        # The savepoint makes a failed executor undo the claim, the lock's changes and the
        # decision together; nothing is stored for a key whose decision did not happen.
        async with self._session.begin_nested():
            stored = await guard.claim()
            if stored is not None:
                result = DecisionResult(stored.status_code, stored.body or {}, replayed=True)
            else:
                result = await self._apply(parent_user_id, approval_id, decision, note, guard)
        await self._session.commit()
        if decision is Decision.deny and not result.replayed:
            await enqueue_best_effort(self._jobs, DENIED_JOB, approval_id=str(approval_id))
        return result

    async def _apply(
        self,
        parent_user_id: UUID,
        approval_id: UUID,
        decision: Decision,
        note: str | None,
        guard: IdempotencyGuard,
    ) -> DecisionResult:
        approval = await self._approvals.lock_by_id_for_parent(
            approval_id, parent_user_id=parent_user_id
        )
        if approval is None:
            raise NotFoundError()
        now = self._clock.now()
        if approval.status is not ApprovalStatus.pending or approval.expires_at <= now:
            raise ConflictError("APPROVAL_NOT_PENDING", "The approval is no longer pending")
        if decision is Decision.approve:
            await self._executor.execute(approval)
            approval.status = ApprovalStatus.approved
        else:
            approval.status = ApprovalStatus.denied
            approval.parent_note = note
        approval.responded_at = now
        await self._session.flush()
        body = approval_view(approval).to_json()
        await guard.store(200, body)
        return DecisionResult(200, body, replayed=False)
