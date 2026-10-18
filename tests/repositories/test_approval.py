from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import new_id
from app.models.identity import ApprovalStatus, PlayerProfile, PurchaseApproval, User
from app.repositories.approval import PurchaseApprovalRepository
from tests.factories import MakeDependent, MakeUser
from tests.fakes import FakeClock


async def _approval(
    repo: PurchaseApprovalRepository,
    trainer: User,
    parent: User,
    child: PlayerProfile,
    expires_at: datetime,
) -> PurchaseApproval:
    return await repo.add(
        trainer_id=trainer.id,
        player_profile_id=child.id,
        parent_user_id=parent.id,
        subject_type="session",
        subject_id=new_id(),
        amount_cents=1500,
        token_amount=None,
        expires_at=expires_at,
    )


async def test_cross_parent_and_cross_tenant_lookups_return_none(
    db_session: AsyncSession,
    make_trainer: MakeUser,
    make_user: MakeUser,
    make_dependent: MakeDependent,
    clock: FakeClock,
) -> None:
    repo = PurchaseApprovalRepository(db_session)
    t1, t2 = await make_trainer(), await make_trainer()
    parent, stranger = await make_user(), await make_user()
    child = await make_dependent(parent=parent)
    row = await _approval(repo, t1, parent, child, clock.now() + timedelta(hours=1))

    assert await repo.lock_by_id_for_parent(row.id, parent_user_id=parent.id) is not None
    assert await repo.lock_by_id_for_parent(row.id, parent_user_id=stranger.id) is None
    assert await repo.get(row.id, trainer_id=t1.id) is not None
    assert await repo.get(row.id, trainer_id=t2.id) is None
    assert [a.id for a in await repo.list_for_trainer(trainer_id=t1.id)] == [row.id]
    assert await repo.list_for_trainer(trainer_id=t2.id) == []


async def test_expire_due_only_touches_pending_rows_past_deadline(
    db_session: AsyncSession,
    make_trainer: MakeUser,
    make_user: MakeUser,
    make_dependent: MakeDependent,
    clock: FakeClock,
) -> None:
    repo = PurchaseApprovalRepository(db_session)
    t = await make_trainer()
    parent = await make_user()
    child = await make_dependent(parent=parent)
    due = await _approval(repo, t, parent, child, clock.now() - timedelta(minutes=1))
    later = await _approval(repo, t, parent, child, clock.now() + timedelta(hours=1))
    done = await _approval(repo, t, parent, child, clock.now() - timedelta(minutes=5))
    done.status = ApprovalStatus.approved
    await db_session.flush()

    assert await repo.expire_due(now=clock.now()) == [due.id]
    for row in (due, later, done):
        await db_session.refresh(row)
    assert due.status is ApprovalStatus.expired
    assert later.status is ApprovalStatus.pending
    assert done.status is ApprovalStatus.approved
    assert await repo.expire_due(now=clock.now()) == []
