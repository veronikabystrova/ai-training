from datetime import timedelta
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import UnprocessableError
from app.core.ids import new_id
from app.models.identity import User, UserRole, UserStatus
from app.repositories.profile import ProfileRepository
from app.repositories.user import UserRepository
from tests.factories import MakeUser
from tests.fakes import FakeClock
from tests.repositories.query_plans import explain_select


async def test_get_by_email_is_case_insensitive(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    repo = UserRepository(db_session)
    u = await make_user(email="Mixed.Case@Example.com")
    found = await repo.get_by_email("mixed.case@example.COM")
    assert found is not None and found.id == u.id
    assert await repo.get_by_email("nobody@example.com") is None


async def test_directory_keyset_pages_cover_all_rows_in_order(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    repo = UserRepository(db_session)
    made = [await make_user(role=UserRole.coach) for _ in range(5)]
    seen: list[object] = []
    cursor = None
    pages = 0
    while True:
        rows, cursor = await repo.list_directory(role=UserRole.coach, cursor=cursor, limit=2)
        seen.extend(r.id for r in rows)
        pages += 1
        if cursor is None:
            break
    assert pages == 3
    expected = [u.id for u in sorted(made, key=lambda u: (u.created_at, u.id), reverse=True)]
    assert seen == expected


async def test_directory_filters_by_query_status_and_role(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    repo = UserRepository(db_session)
    ann = await make_user(email="ann@zeta.test", first_name="Ann", last_name="Lee")
    await make_user(email="bob@zeta.test", first_name="Bob", last_name="Ray")
    await make_user(
        email="inactive@zeta.test", first_name="Ann", last_name="Gone", status=UserStatus.inactive
    )
    by_name, _ = await repo.list_directory(query="ann l", limit=10)
    assert [u.id for u in by_name] == [ann.id]
    by_email, _ = await repo.list_directory(query="@zeta", status=UserStatus.active, limit=10)
    assert {u.email for u in by_email} == {"ann@zeta.test", "bob@zeta.test"}
    none, _ = await repo.list_directory(query="@zeta", role=UserRole.trainer, limit=10)
    assert none == []


async def test_directory_query_is_literal_not_a_pattern(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    repo = UserRepository(db_session)
    await make_user(email="plain@wild.test")
    rows, _ = await repo.list_directory(query="%%", limit=10)
    assert rows == []


async def test_directory_query_needs_two_characters(db_session: AsyncSession) -> None:
    with pytest.raises(UnprocessableError):
        await UserRepository(db_session).list_directory(query="a", limit=10)


async def test_set_status(db_session: AsyncSession, make_user: MakeUser) -> None:
    repo = UserRepository(db_session)
    u = await make_user()
    assert await repo.set_status(u.id, UserStatus.inactive) is True
    await db_session.refresh(u)
    assert u.status is UserStatus.inactive
    assert await repo.set_status(UUID(int=0), UserStatus.active) is False


async def test_anonymise_scrubs_user_and_profile(
    db_session: AsyncSession, make_user: MakeUser, clock: FakeClock
) -> None:
    repo = UserRepository(db_session)
    u = await make_user(email="real@example.com", first_name="Real", last_name="Person")
    await repo.anonymise(u.id, email=f"erased-{u.id}@erased.invalid", now=clock.now())
    await db_session.refresh(u)
    profile = await ProfileRepository(db_session).get(u.id)
    assert profile is not None
    await db_session.refresh(profile)
    assert u.email == f"erased-{u.id}@erased.invalid"
    assert u.status is UserStatus.deleted and u.deleted_at == clock.now()
    assert u.password_hash == "!" and u.is_email_verified is False
    assert (profile.first_name, profile.phone, profile.avatar_key) == ("Deleted", None, None)
    assert await repo.get_by_email("real@example.com") is None


async def test_directory_query_uses_both_trigram_indexes(db_session: AsyncSession) -> None:
    plan = await explain_select(
        db_session, UserRepository(db_session).list_directory(query="abc", limit=10)
    )
    assert "ix_users_email_trgm" in plan, plan
    assert "ix_profiles_full_name_trgm" in plan, plan


async def test_directory_keeps_users_without_a_profile(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    repo = UserRepository(db_session)
    bare = User(
        id=new_id(),
        email="bare@zeta.test",
        password_hash="x",
        role=UserRole.super_admin,
    )
    await repo.add(bare)
    rows, _ = await repo.list_directory(role=UserRole.super_admin, limit=10)
    assert [u.id for u in rows] == [bare.id]


async def test_directory_keyset_with_distinct_created_at(
    db_session: AsyncSession, make_user: MakeUser, clock: FakeClock
) -> None:
    repo = UserRepository(db_session)
    made = [await make_user(role=UserRole.coach) for _ in range(5)]
    for i, u in enumerate(made):  # distinct timestamps, deliberately out of id order
        u.created_at = clock.now() + timedelta(minutes=(i * 7) % 5)
    await db_session.flush()
    seen: list[UUID] = []
    cursor = None
    while True:
        rows, cursor = await repo.list_directory(role=UserRole.coach, cursor=cursor, limit=2)
        seen.extend(r.id for r in rows)
        if cursor is None:
            break
    expected = [u.id for u in sorted(made, key=lambda u: (u.created_at, u.id), reverse=True)]
    assert seen == expected
    assert len({u.created_at for u in made}) > 1


async def test_add_persists_user(db_session: AsyncSession) -> None:
    repo = UserRepository(db_session)
    user = await repo.add(
        User(id=new_id(), email="added@zeta.test", password_hash="x", role=UserRole.player)
    )
    assert (await repo.get(user.id)) is user
