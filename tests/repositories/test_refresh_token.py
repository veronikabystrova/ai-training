from datetime import timedelta

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import new_id
from app.core.security import hash_token
from app.repositories.refresh_token import RefreshTokenRepository
from tests.factories import MakeUser
from tests.fakes import FakeClock


async def test_rotate_marks_old_consumed_and_keeps_family(
    db_session: AsyncSession, make_user: MakeUser, clock: FakeClock
) -> None:
    repo = RefreshTokenRepository(db_session)
    u = await make_user()
    family = new_id()
    raw = await repo.issue(
        user_id=u.id, family_id=family, expires_at=clock.now() + timedelta(days=30)
    )
    old = await repo.lock_by_hash(hash_token(raw))
    assert old is not None and old.consumed_at is None
    new_raw = await repo.rotate(old, now=clock.now(), expires_at=clock.now() + timedelta(days=30))
    assert old.consumed_at == clock.now()
    fresh = await repo.lock_by_hash(hash_token(new_raw))
    assert fresh is not None and fresh.family_id == family and fresh.user_id == u.id


async def test_revoke_family_revokes_all_live_rows_only_in_family(
    db_session: AsyncSession, make_user: MakeUser, clock: FakeClock
) -> None:
    repo = RefreshTokenRepository(db_session)
    u = await make_user()
    fam, other = new_id(), new_id()
    exp = clock.now() + timedelta(days=30)
    a = await repo.issue(user_id=u.id, family_id=fam, expires_at=exp)
    b = await repo.issue(user_id=u.id, family_id=fam, expires_at=exp)
    c = await repo.issue(user_id=u.id, family_id=other, expires_at=exp)
    assert await repo.revoke_family(fam, now=clock.now()) == 2
    assert await repo.revoke_family(fam, now=clock.now()) == 0
    for raw, revoked in ((a, True), (b, True), (c, False)):
        row = await repo.lock_by_hash(hash_token(raw))
        assert row is not None and (row.revoked_at is not None) is revoked


async def test_lock_by_hash_uses_for_update(
    db_session: AsyncSession, make_user: MakeUser, clock: FakeClock
) -> None:
    repo = RefreshTokenRepository(db_session)
    u = await make_user()
    raw = await repo.issue(
        user_id=u.id, family_id=new_id(), expires_at=clock.now() + timedelta(days=1)
    )
    seen: list[str] = []

    def _capture(_c: object, _cur: object, statement: str, *_a: object) -> None:
        seen.append(statement)

    sync_engine = db_session.bind.sync_engine  # type: ignore[union-attr]
    event.listen(sync_engine, "before_cursor_execute", _capture)
    try:
        assert await repo.lock_by_hash(hash_token(raw)) is not None
    finally:
        event.remove(sync_engine, "before_cursor_execute", _capture)
    assert any("FOR UPDATE" in s for s in seen)


async def test_lock_by_hash_unknown_returns_none(db_session: AsyncSession) -> None:
    assert await RefreshTokenRepository(db_session).lock_by_hash(hash_token("nope")) is None


async def test_purge_removes_only_expired(
    db_session: AsyncSession, make_user: MakeUser, clock: FakeClock
) -> None:
    repo = RefreshTokenRepository(db_session)
    u = await make_user()
    await repo.issue(user_id=u.id, family_id=new_id(), expires_at=clock.now() - timedelta(days=1))
    live = await repo.issue(
        user_id=u.id, family_id=new_id(), expires_at=clock.now() + timedelta(days=1)
    )
    assert await repo.purge(before=clock.now()) == 1
    assert await repo.lock_by_hash(hash_token(live)) is not None
