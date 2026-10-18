from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.identity import ShareLinkKind
from app.repositories.share_link import ShareLinkRepository
from tests.factories import AddShareLink, MakeUser
from tests.fakes import FakeClock


async def test_increment_use_stops_at_max_uses(
    db_session: AsyncSession, make_trainer: MakeUser, add_share_link: AddShareLink
) -> None:
    repo = ShareLinkRepository(db_session)
    t = await make_trainer()
    link = await add_share_link(t, kind="coach_unique", target_email="c@example.com")
    assert link.max_uses == 1
    assert await repo.increment_use(link.id, trainer_id=t.id) is True
    assert await repo.increment_use(link.id, trainer_id=t.id) is False


async def test_increment_use_unlimited_when_max_uses_null(
    db_session: AsyncSession, make_trainer: MakeUser, add_share_link: AddShareLink
) -> None:
    repo = ShareLinkRepository(db_session)
    t = await make_trainer()
    link = await add_share_link(t)
    for _ in range(3):
        assert await repo.increment_use(link.id, trainer_id=t.id) is True


async def test_increment_use_is_tenant_scoped(
    db_session: AsyncSession, make_trainer: MakeUser, add_share_link: AddShareLink
) -> None:
    repo = ShareLinkRepository(db_session)
    t1, t2 = await make_trainer(), await make_trainer()
    link = await add_share_link(t1)
    assert await repo.increment_use(link.id, trainer_id=t2.id) is False


async def test_lock_by_code_and_get_scoping(
    db_session: AsyncSession, make_trainer: MakeUser, add_share_link: AddShareLink
) -> None:
    repo = ShareLinkRepository(db_session)
    t1, t2 = await make_trainer(), await make_trainer()
    link = await add_share_link(t1)
    locked = await repo.lock_by_code(link.code)
    assert locked is not None and locked.id == link.id
    assert await repo.lock_by_code("missing") is None
    assert await repo.get(link.id, trainer_id=t1.id) is not None
    assert await repo.get(link.id, trainer_id=t2.id) is None


async def test_add_list_and_deactivate(
    db_session: AsyncSession, make_trainer: MakeUser, clock: FakeClock
) -> None:
    repo = ShareLinkRepository(db_session)
    t1, t2 = await make_trainer(), await make_trainer()
    link = await repo.add(
        trainer_id=t1.id,
        created_by_user_id=t1.id,
        code="abc123",
        kind=ShareLinkKind.coach_unique,
        target_email="c@example.com",
        max_uses=1,
        expires_at=clock.now() + timedelta(days=7),
    )
    assert [r.id for r in await repo.list_for_trainer(trainer_id=t1.id)] == [link.id]
    assert await repo.list_for_trainer(trainer_id=t2.id) == []
    assert await repo.deactivate(link.id, trainer_id=t2.id) is False
    assert await repo.deactivate(link.id, trainer_id=t1.id) is True
    await db_session.refresh(link)
    assert link.is_active is False
