from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.identity import OttPurpose
from app.repositories.one_time_token import OneTimeTokenRepository
from tests.factories import MakeUser
from tests.fakes import FakeClock


async def test_consume_is_single_use(
    db_session: AsyncSession, make_user: MakeUser, clock: FakeClock
) -> None:
    repo = OneTimeTokenRepository(db_session)
    u = await make_user()
    raw = await repo.issue(
        user_id=u.id, purpose=OttPurpose.password_reset, ttl=timedelta(hours=1), now=clock.now()
    )
    first = await repo.consume(raw=raw, purpose=OttPurpose.password_reset, now=clock.now())
    assert first is not None and first.user_id == u.id
    assert await repo.consume(raw=raw, purpose=OttPurpose.password_reset, now=clock.now()) is None


async def test_wrong_purpose_and_expired_do_not_consume(
    db_session: AsyncSession, make_user: MakeUser, clock: FakeClock
) -> None:
    repo = OneTimeTokenRepository(db_session)
    u = await make_user()
    raw = await repo.issue(
        user_id=u.id, purpose=OttPurpose.email_verify, ttl=timedelta(hours=24), now=clock.now()
    )
    assert await repo.consume(raw=raw, purpose=OttPurpose.password_reset, now=clock.now()) is None
    clock.advance(hours=24, seconds=1)
    assert await repo.consume(raw=raw, purpose=OttPurpose.email_verify, now=clock.now()) is None


async def test_issue_supersedes_open_tokens(
    db_session: AsyncSession, make_user: MakeUser, clock: FakeClock
) -> None:
    repo = OneTimeTokenRepository(db_session)
    u = await make_user()
    old = await repo.issue(
        user_id=u.id, purpose=OttPurpose.password_reset, ttl=timedelta(hours=1), now=clock.now()
    )
    await repo.issue(
        user_id=u.id, purpose=OttPurpose.password_reset, ttl=timedelta(hours=1), now=clock.now()
    )
    assert await repo.consume(raw=old, purpose=OttPurpose.password_reset, now=clock.now()) is None


async def test_issue_does_not_supersede_other_purpose_or_user(
    db_session: AsyncSession, make_user: MakeUser, clock: FakeClock
) -> None:
    repo = OneTimeTokenRepository(db_session)
    u, other = await make_user(), await make_user()
    verify = await repo.issue(
        user_id=u.id, purpose=OttPurpose.email_verify, ttl=timedelta(hours=1), now=clock.now()
    )
    theirs = await repo.issue(
        user_id=other.id, purpose=OttPurpose.password_reset, ttl=timedelta(hours=1), now=clock.now()
    )
    await repo.issue(
        user_id=u.id, purpose=OttPurpose.password_reset, ttl=timedelta(hours=1), now=clock.now()
    )
    assert (
        await repo.consume(raw=verify, purpose=OttPurpose.email_verify, now=clock.now()) is not None
    )
    assert (
        await repo.consume(raw=theirs, purpose=OttPurpose.password_reset, now=clock.now())
        is not None
    )


async def test_payload_roundtrip_and_purge(
    db_session: AsyncSession, make_user: MakeUser, clock: FakeClock
) -> None:
    repo = OneTimeTokenRepository(db_session)
    u = await make_user()
    raw = await repo.issue(
        user_id=u.id,
        purpose=OttPurpose.email_change,
        ttl=timedelta(hours=1),
        now=clock.now(),
        payload={"new_email": "n@example.com"},
    )
    consumed = await repo.consume(raw=raw, purpose=OttPurpose.email_change, now=clock.now())
    assert consumed is not None and consumed.payload == {"new_email": "n@example.com"}
    assert await repo.purge(before=clock.now() + timedelta(hours=2)) == 1
    assert await repo.purge(before=clock.now() + timedelta(hours=2)) == 0
