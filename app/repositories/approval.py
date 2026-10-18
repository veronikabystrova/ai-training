from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.identity import ApprovalStatus, PurchaseApproval


class PurchaseApprovalRepository:
    """Trainer-scoped (``trainer_id`` required) or parent-scoped (``lock_by_id_for_parent``).

    ``expire_due`` is the one cross-tenant method: a system sweep with a per-row status guard.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add(
        self,
        *,
        trainer_id: UUID,
        player_profile_id: UUID,
        parent_user_id: UUID,
        subject_type: str,
        subject_id: UUID,
        amount_cents: int | None,
        token_amount: int | None,
        expires_at: datetime,
    ) -> PurchaseApproval:
        row = PurchaseApproval(
            trainer_id=trainer_id,
            player_profile_id=player_profile_id,
            parent_user_id=parent_user_id,
            subject_type=subject_type,
            subject_id=subject_id,
            amount_cents=amount_cents,
            token_amount=token_amount,
            expires_at=expires_at,
        )
        self._s.add(row)
        await self._s.flush()
        return row

    async def get(self, approval_id: UUID, *, trainer_id: UUID) -> PurchaseApproval | None:
        result = await self._s.execute(
            select(PurchaseApproval).where(
                PurchaseApproval.id == approval_id, PurchaseApproval.trainer_id == trainer_id
            )
        )
        return result.scalar_one_or_none()

    async def list_for_trainer(
        self, *, trainer_id: UUID, status: ApprovalStatus | None = None
    ) -> list[PurchaseApproval]:
        stmt = (
            select(PurchaseApproval)
            .where(PurchaseApproval.trainer_id == trainer_id)
            .order_by(PurchaseApproval.requested_at.desc(), PurchaseApproval.id.desc())
        )
        if status is not None:
            stmt = stmt.where(PurchaseApproval.status == status)
        return list((await self._s.execute(stmt)).scalars())

    async def lock_by_id_for_parent(
        self, approval_id: UUID, *, parent_user_id: UUID
    ) -> PurchaseApproval | None:
        """``FOR UPDATE`` so concurrent approve/deny calls serialise; another parent gets None."""
        result = await self._s.execute(
            select(PurchaseApproval)
            .where(
                PurchaseApproval.id == approval_id,
                PurchaseApproval.parent_user_id == parent_user_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def expire_due(self, *, now: datetime) -> list[UUID]:
        """Move pending approvals past their deadline to ``expired``; returns their ids."""
        result = await self._s.execute(
            update(PurchaseApproval)
            .where(
                PurchaseApproval.status == ApprovalStatus.pending,
                PurchaseApproval.expires_at <= now,
            )
            .values(status=ApprovalStatus.expired, responded_at=now)
            .returning(PurchaseApproval.id)
            .execution_options(synchronize_session=False)
        )
        return list(result.scalars())
