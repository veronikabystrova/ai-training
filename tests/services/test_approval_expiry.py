from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError
from app.repositories.idempotency import IdempotencyRepository
from app.services.approval_service import ApprovalService, Decision
from app.services.idempotency_service import IdempotencyGuard
from tests.factories import LinkPlayer, MakeApproval, MakeDependent, MakeTrainer, MakeUser
from tests.fakes import FakeClock, FakeJobQueue, FakePurchaseExecutor


@pytest.mark.parametrize(
    ("elapsed", "decidable"),
    [(timedelta(hours=47, minutes=59), True), (timedelta(hours=48), False)],
)
async def test_approval_expires_after_48h_boundary(
    elapsed: timedelta,
    decidable: bool,
    clock: FakeClock,
    db_session: AsyncSession,
    make_user: MakeUser,
    make_dependent: MakeDependent,
    make_trainer: MakeTrainer,
    link_player: LinkPlayer,
    make_approval: MakeApproval,
) -> None:
    parent = await make_user()
    trainer = await make_trainer()
    child = await make_dependent(parent=parent)
    approval = await make_approval(parent=parent, trainer=trainer, player=child)  # now + 48h
    executor = FakePurchaseExecutor()
    service = ApprovalService(
        session=db_session, clock=clock, executor=executor, jobs=FakeJobQueue()
    )
    clock.advance(seconds=elapsed.total_seconds())
    guard = IdempotencyGuard(
        IdempotencyRepository(db_session),
        key="k",
        principal_id=parent.id,
        endpoint="/x",
        request_hash="h",
        expires_at=clock.now() + timedelta(hours=24),
    )

    call = service.decide(
        parent_user_id=parent.id,
        approval_id=approval.id,
        decision=Decision.approve,
        note=None,
        guard=guard,
    )
    if decidable:
        assert (await call).status_code == 200
    else:
        with pytest.raises(ConflictError) as exc:
            await call
        assert exc.value.code == "APPROVAL_NOT_PENDING"
