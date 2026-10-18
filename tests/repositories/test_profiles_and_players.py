from datetime import date
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.player import PlayerProfileRepository
from app.repositories.profile import ProfileRepository, TrainerProfileRepository
from tests.factories import MakeDependent, MakePlayer, MakeUser


async def test_profile_and_trainer_slug_lookup(
    db_session: AsyncSession, make_user: MakeUser, make_trainer: MakeUser
) -> None:
    u = await make_user(first_name="Zed")
    profile = await ProfileRepository(db_session).get(u.id)
    assert profile is not None and profile.first_name == "Zed"
    assert await ProfileRepository(db_session).get(UUID(int=0)) is None

    t = await make_trainer()
    repo = TrainerProfileRepository(db_session)
    row = await repo.get(t.id)
    assert row is not None
    by_slug = await repo.get_by_slug(row.slug)
    assert by_slug is not None and by_slug.user_id == t.id
    assert await repo.get_by_slug("no-such-slug") is None


async def test_player_lookups(
    db_session: AsyncSession,
    make_user: MakeUser,
    make_player: MakePlayer,
    make_dependent: MakeDependent,
) -> None:
    repo = PlayerProfileRepository(db_session)
    parent = await make_user()
    own = await make_player(owner=parent)
    child = await make_dependent(parent=parent, with_login=True)
    assert child.login_user_id is not None

    owned = await repo.list_by_owner(parent.id)
    assert {p.id for p in owned} == {own.id, child.id}
    by_login = await repo.get_by_login_user(child.login_user_id)
    assert by_login is not None and by_login.id == child.id
    assert await repo.get_by_login_user(parent.id) is None
    got = await repo.get(own.id)
    assert got is not None and got.owner_user_id == parent.id


async def test_duplicate_check_matches_owner_name_and_birth_date(
    db_session: AsyncSession, make_user: MakeUser, make_player: MakePlayer
) -> None:
    repo = PlayerProfileRepository(db_session)
    parent = await make_user()
    p = await make_player(owner=parent, display_name="Kid One")
    assert await repo.find_duplicate(
        owner_user_id=parent.id, display_name="kid one", birth_date=p.birth_date
    )
    assert not await repo.find_duplicate(
        owner_user_id=parent.id, display_name="Kid One", birth_date=date(2000, 1, 1)
    )
    other = await make_user()
    assert not await repo.find_duplicate(
        owner_user_id=other.id, display_name="Kid One", birth_date=p.birth_date
    )
