from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.coach_profile import CoachProfileRepository
from tests.factories import MakeCoach, MakeUser
from tests.fakes import FakeClock
from tests.repositories.query_plans import explain_select


async def test_get_active_is_tenant_scoped(
    db_session: AsyncSession, make_trainer: MakeUser, make_coach: MakeCoach
) -> None:
    repo = CoachProfileRepository(db_session)
    t1, t2 = await make_trainer(), await make_trainer()
    coach = await make_coach(t1)
    assert await repo.get_active(trainer_id=t1.id, user_id=coach.user_id) is not None
    assert await repo.get_active(trainer_id=t2.id, user_id=coach.user_id) is None


async def test_get_active_returns_none_after_tenure_closes(
    db_session: AsyncSession, make_trainer: MakeUser, make_coach: MakeCoach, clock: FakeClock
) -> None:
    repo = CoachProfileRepository(db_session)
    t = await make_trainer()
    coach = await make_coach(t)
    assert await repo.close_tenure(trainer_id=t.id, user_id=coach.user_id, now=clock.now())
    assert await repo.get_active(trainer_id=t.id, user_id=coach.user_id) is None
    assert not await repo.close_tenure(trainer_id=t.id, user_id=coach.user_id, now=clock.now())


async def test_active_tenure_for_user_and_listing(
    db_session: AsyncSession, make_trainer: MakeUser, make_coach: MakeCoach, clock: FakeClock
) -> None:
    repo = CoachProfileRepository(db_session)
    t1, t2 = await make_trainer(), await make_trainer()
    coach = await make_coach(t1)
    other = await make_coach(t2)
    found = await repo.get_active_for_user(coach.user_id)
    assert found is not None and found.trainer_id == t1.id
    listed = await repo.list_active(trainer_id=t1.id)
    assert [c.user_id for c in listed] == [coach.user_id]
    assert other.user_id not in {c.user_id for c in listed}


async def test_active_lookups_use_the_partial_indexes(
    db_session: AsyncSession, make_trainer: MakeUser, make_coach: MakeCoach
) -> None:
    """``is_active IS true`` cannot prove the ``WHERE is_active`` index predicate (seq scan)."""
    repo = CoachProfileRepository(db_session)
    t = await make_trainer()
    coach = await make_coach(t)

    by_user = await explain_select(db_session, repo.get_active_for_user(coach.user_id))
    assert "uq_coach_one_active_trainer" in by_user, by_user
    in_tenant = await explain_select(
        db_session, repo.get_active(trainer_id=t.id, user_id=coach.user_id)
    )
    assert "Seq Scan" not in in_tenant, in_tenant
    listing = await explain_select(db_session, repo.list_active(trainer_id=t.id))
    assert "Seq Scan" not in listing, listing
