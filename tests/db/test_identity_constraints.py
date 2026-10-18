import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import constraint_name, map_integrity_error
from tests.factories import (
    AddShareLink,
    AddWindow,
    LinkPlayer,
    MakeCoach,
    MakeDependent,
    MakePlayer,
    MakeTrainer,
    MakeUser,
)
from tests.fakes import FakeClock


def _constraint(exc: IntegrityError) -> str | None:
    return constraint_name(exc)


async def test_real_asyncpg_error_maps_to_domain_error(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    """Regression: asyncpg has no ``orig.diag``; the mapping must work on a real driver error."""
    await make_user(email="dup@x.com")
    with pytest.raises(IntegrityError) as e:
        await make_user(email="DUP@x.com")
    mapped = map_integrity_error(e.value)
    assert mapped is not None
    assert mapped.code == "EMAIL_TAKEN"
    assert mapped.status == 409


async def test_email_unique_case_insensitive(db_session: AsyncSession, make_user: MakeUser) -> None:
    await make_user(email="A@x.com")
    with pytest.raises(IntegrityError) as e:
        await make_user(email="a@X.com")
    assert _constraint(e.value) == "uq_users_email"


async def test_one_active_coach_tenure(
    db_session: AsyncSession, make_trainer: MakeTrainer, make_coach: MakeCoach
) -> None:
    t1, t2 = await make_trainer(), await make_trainer()
    coach = await make_coach(t1)
    with pytest.raises(IntegrityError) as e:
        await make_coach(t2, user=coach.user)
    assert _constraint(e.value) == "uq_coach_one_active_trainer"


async def test_inactive_tenures_do_not_collide(
    db_session: AsyncSession, make_trainer: MakeTrainer, make_coach: MakeCoach, clock: FakeClock
) -> None:
    t1, t2 = await make_trainer(), await make_trainer()
    c = await make_coach(t1)
    c.is_active, c.left_at = False, clock.now()
    await db_session.flush()
    await make_coach(t2, user=c.user)  # ok


async def test_coach_left_iff_inactive(
    db_session: AsyncSession, make_trainer: MakeTrainer, make_coach: MakeCoach, clock: FakeClock
) -> None:
    c = await make_coach(await make_trainer())
    c.left_at = clock.now()  # still is_active
    with pytest.raises(IntegrityError) as e:
        await db_session.flush()
    assert _constraint(e.value) == "ck_coach_left_iff_inactive"


async def test_availability_overlap_rejected_touching_allowed(
    db_session: AsyncSession, make_player: MakePlayer, add_window: AddWindow
) -> None:
    p = await make_player()
    await add_window(p.id, weekday=0, start="09:00", end="10:00")
    await add_window(p.id, weekday=0, start="10:00", end="11:00")  # touching ok
    await add_window(p.id, weekday=1, start="10:30", end="11:30")  # other weekday ok
    with pytest.raises(IntegrityError) as e:
        await add_window(p.id, weekday=0, start="10:30", end="11:30")
    assert _constraint(e.value) == "ex_availability_no_overlap"


async def test_availability_start_before_end(
    db_session: AsyncSession, make_player: MakePlayer, add_window: AddWindow
) -> None:
    p = await make_player()
    with pytest.raises(IntegrityError) as e:
        await add_window(p.id, weekday=0, start="10:00", end="10:00")
    assert _constraint(e.value) == "ck_availability_start_before_end"


async def test_share_link_kind_checks(
    db_session: AsyncSession, make_trainer: MakeTrainer, add_share_link: AddShareLink
) -> None:
    t = await make_trainer()
    with pytest.raises(IntegrityError) as e:
        await add_share_link(t, kind="coach_unique", target_email=None)
    assert _constraint(e.value) == "ck_share_links_kind_fields"


async def test_static_share_link_forbids_target_email(
    db_session: AsyncSession, make_trainer: MakeTrainer, add_share_link: AddShareLink
) -> None:
    t = await make_trainer()
    with pytest.raises(IntegrityError) as e:
        await add_share_link(t, kind="player_static", target_email="x@y.com")
    assert _constraint(e.value) == "ck_share_links_kind_fields"


async def test_valid_share_links_accepted(
    db_session: AsyncSession, make_trainer: MakeTrainer, add_share_link: AddShareLink
) -> None:
    t = await make_trainer()
    await add_share_link(t, kind="player_static")
    await add_share_link(t, kind="coach_unique", target_email="c@y.com")


async def test_fk_restrict(db_session: AsyncSession, make_trainer: MakeTrainer) -> None:
    t = await make_trainer()
    with pytest.raises(IntegrityError):
        await db_session.execute(text("delete from users where id = :i"), {"i": t.id})


async def test_hex_colour_check(db_session: AsyncSession, make_trainer: MakeTrainer) -> None:
    t = await make_trainer()
    with pytest.raises(IntegrityError) as e:
        await db_session.execute(
            text("update trainer_profiles set brand_primary_color='red' where user_id=:i"),
            {"i": t.id},
        )
    assert _constraint(e.value) == "ck_trainer_profiles_brand_hex"


async def test_trainer_slug_unique(db_session: AsyncSession, make_trainer: MakeTrainer) -> None:
    t = await make_trainer()
    other = await make_trainer()
    with pytest.raises(IntegrityError) as e:
        await db_session.execute(
            text(
                "update trainer_profiles set slug = (select slug from trainer_profiles "
                "where user_id=:a) where user_id=:b"
            ),
            {"a": t.id, "b": other.id},
        )
    assert _constraint(e.value) == "uq_trainer_profiles_slug"


async def test_trainer_player_link_unique(
    db_session: AsyncSession,
    make_trainer: MakeTrainer,
    make_player: MakePlayer,
    link_player: LinkPlayer,
) -> None:
    t, p = await make_trainer(), await make_player()
    await link_player(t, p)
    with pytest.raises(IntegrityError) as e:
        await link_player(t, p)
    assert _constraint(e.value) == "uq_trainer_player_links_trainer_player"


async def test_dependent_has_owner_and_optional_login(
    db_session: AsyncSession, make_dependent: MakeDependent
) -> None:
    child = await make_dependent(with_login=True)
    assert child.is_dependent and child.owner_user_id != child.login_user_id


async def test_gender_enum_rejects_unknown_value(
    db_session: AsyncSession, make_player: MakePlayer
) -> None:
    player = await make_player()
    with pytest.raises(DBAPIError, match="invalid input value for enum gender"):
        await db_session.execute(
            text("UPDATE player_profiles SET gender = 'robot' WHERE id = :id"), {"id": player.id}
        )
