from typing import Any

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.identity import Profile, User, UserRole
from app.repositories.availability import AvailabilityRepository
from tests.api.helpers import bearer
from tests.factories import (
    AddWindow,
    MakeCoach,
    MakeDependent,
    MakePlayer,
    MakeTrainer,
    MakeUser,
    PrincipalToken,
)

AVAILABILITY = "/api/v1/me/availability"


def dependent_url(player_id: Any) -> str:
    return f"/api/v1/me/players/{player_id}/availability"


def week(*windows: tuple[int, str, str], timezone: str = "Europe/Paris") -> dict[str, Any]:
    return {
        "timezone": timezone,
        "windows": [{"weekday": d, "start_time": s, "end_time": e} for d, s, e in windows],
    }


def code(response: Any) -> str:
    return str(response.json()["error"]["code"])


async def test_get_empty_then_put_replaces_whole_week_atomically(
    client: AsyncClient,
    make_user: MakeUser,
    make_player: MakePlayer,
    add_window: AddWindow,
    principal_token: PrincipalToken,
    db_session: AsyncSession,
) -> None:
    user = await make_user()
    profile = await make_player(owner=user)
    headers = bearer(principal_token(user))

    r = await client.get(AVAILABILITY, headers=headers)
    assert (r.status_code, r.json()) == (200, {"timezone": "UTC", "windows": []})

    await add_window(profile.id, weekday=4, start="07:00", end="08:00")
    r = await client.put(
        AVAILABILITY,
        json=week((2, "15:00", "16:30"), (0, "09:00", "10:00"), (0, "10:00", "11:00")),
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json() == {
        "timezone": "Europe/Paris",
        "windows": [
            {"weekday": 0, "start_time": "09:00", "end_time": "10:00"},  # touching is allowed
            {"weekday": 0, "start_time": "10:00", "end_time": "11:00"},
            {"weekday": 2, "start_time": "15:00", "end_time": "16:30"},
        ],
    }
    assert (await client.get(AVAILABILITY, headers=headers)).json() == r.json()
    db_profile = await db_session.get(Profile, user.id)
    assert db_profile is not None
    assert db_profile.timezone == "Europe/Paris"

    cleared = await client.put(AVAILABILITY, json=week(), headers=headers)
    assert cleared.json()["windows"] == []


async def test_put_overlap_422_overlapping_windows_and_old_set_intact(
    client: AsyncClient,
    make_user: MakeUser,
    make_player: MakePlayer,
    add_window: AddWindow,
    principal_token: PrincipalToken,
    db_session: AsyncSession,
) -> None:
    user = await make_user()
    profile = await make_player(owner=user)
    await add_window(profile.id, weekday=1, start="09:00", end="10:00")
    headers = bearer(principal_token(user))

    r = await client.put(
        AVAILABILITY,
        json=week((0, "09:00", "10:00"), (0, "09:59", "11:00"), timezone="Asia/Tokyo"),
        headers=headers,
    )

    assert (r.status_code, code(r)) == (422, "OVERLAPPING_WINDOWS")
    rows = await AvailabilityRepository(db_session).get_by_subject(
        subject_type="player", subject_id=profile.id
    )
    assert [(w.weekday, w.start_time.isoformat()) for w in rows] == [(1, "09:00:00")]
    db_profile = await db_session.get(Profile, user.id)
    assert db_profile is not None
    assert db_profile.timezone == "UTC"  # the timezone change rolled back with it


async def test_put_validation_errors_422(
    client: AsyncClient,
    make_user: MakeUser,
    make_player: MakePlayer,
    principal_token: PrincipalToken,
) -> None:
    user = await make_user()
    await make_player(owner=user)
    headers = bearer(principal_token(user))
    bad_bodies: list[dict[str, Any]] = [
        week((0, "10:00", "10:00")),  # start == end
        week((0, "11:00", "10:00")),  # start > end
        week((7, "09:00", "10:00")),  # weekday
        week((0, "09:00:30", "10:00")),  # not minute precision
        week((0, "09:00", "10:00"), timezone="Mars/Olympus"),
        {**week(), "extra": 1},
        {"windows": []},  # timezone is required
    ]
    for body in bad_bodies:
        r = await client.put(AVAILABILITY, json=body, headers=headers)
        assert (r.status_code, code(r)) == (422, "VALIDATION_ERROR"), body


async def test_put_over_70_windows_422(
    client: AsyncClient,
    make_user: MakeUser,
    make_player: MakePlayer,
    principal_token: PrincipalToken,
) -> None:
    user = await make_user()
    await make_player(owner=user)
    headers = bearer(principal_token(user))
    windows = [(d, f"{h:02d}:00", f"{h:02d}:30") for d in range(7) for h in range(10)]

    at_limit = await client.put(AVAILABILITY, json=week(*windows), headers=headers)
    assert at_limit.status_code == 200
    assert len(at_limit.json()["windows"]) == 70

    over = await client.put(
        AVAILABILITY, json=week(*windows, (0, "20:00", "21:00")), headers=headers
    )
    assert (over.status_code, code(over)) == (422, "VALIDATION_ERROR")


async def test_coach_has_own_availability_and_does_not_touch_players(
    client: AsyncClient,
    make_trainer: MakeTrainer,
    make_coach: MakeCoach,
    principal_token: PrincipalToken,
) -> None:
    trainer = await make_trainer()
    tenure = await make_coach(trainer)
    headers = bearer(principal_token(tenure.user, trainer_id=trainer.id))

    r = await client.put(AVAILABILITY, json=week((3, "18:00", "20:00")), headers=headers)
    assert r.status_code == 200
    assert (await client.get(AVAILABILITY, headers=headers)).json()["windows"] == [
        {"weekday": 3, "start_time": "18:00", "end_time": "20:00"}
    ]


async def test_child_reads_and_writes_only_its_own_set(
    client: AsyncClient,
    make_user: MakeUser,
    make_dependent: MakeDependent,
    principal_token: PrincipalToken,
    db_session: AsyncSession,
) -> None:
    parent = await make_user()
    child_profile = await make_dependent(parent=parent, with_login=True)
    sibling = await make_dependent(parent=parent)
    child_user = await db_session.get(User, child_profile.login_user_id)
    assert child_user is not None
    headers = bearer(principal_token(child_user, child=True))

    r = await client.put(
        AVAILABILITY, json=week((5, "10:00", "11:00"), timezone="UTC"), headers=headers
    )
    assert r.status_code == 200
    assert (await client.get(AVAILABILITY, headers=headers)).json()["windows"] == r.json()[
        "windows"
    ]
    rows = await AvailabilityRepository(db_session).get_by_subject(
        subject_type="player", subject_id=child_profile.id
    )
    assert len(rows) == 1
    assert (
        await AvailabilityRepository(db_session).get_by_subject(
            subject_type="player", subject_id=sibling.id
        )
        == []
    )


async def test_trainer_and_super_admin_403(
    client: AsyncClient,
    make_user: MakeUser,
    make_trainer: MakeTrainer,
    principal_token: PrincipalToken,
) -> None:
    trainer = await make_trainer()
    admin = await make_user(role=UserRole.super_admin)
    for user in (trainer, admin):
        headers = bearer(principal_token(user))
        assert (await client.get(AVAILABILITY, headers=headers)).status_code == 403
        r = await client.put(AVAILABILITY, json=week(), headers=headers)
        assert (r.status_code, code(r)) == (403, "FORBIDDEN")


async def test_availability_requires_authentication(client: AsyncClient) -> None:
    assert (await client.get(AVAILABILITY)).status_code == 401
    assert (await client.put(AVAILABILITY, json=week())).status_code == 401


async def test_dependent_availability_parent_replaces_dependents_set(
    client: AsyncClient,
    make_user: MakeUser,
    make_dependent: MakeDependent,
    principal_token: PrincipalToken,
    db_session: AsyncSession,
) -> None:
    parent = await make_user()
    child = await make_dependent(parent=parent)
    headers = bearer(principal_token(parent))

    r = await client.put(
        dependent_url(child.id), json=week((6, "08:00", "09:00"), timezone="UTC"), headers=headers
    )

    assert r.status_code == 200
    assert r.json()["windows"] == [{"weekday": 6, "start_time": "08:00", "end_time": "09:00"}]
    rows = await AvailabilityRepository(db_session).get_by_subject(
        subject_type="player", subject_id=child.id
    )
    assert len(rows) == 1
    overlap = await client.put(
        dependent_url(child.id),
        json=week((0, "09:00", "10:00"), (0, "09:30", "10:30"), timezone="UTC"),
        headers=headers,
    )
    assert (overlap.status_code, code(overlap)) == (422, "OVERLAPPING_WINDOWS")
    assert (
        len(
            await AvailabilityRepository(db_session).get_by_subject(
                subject_type="player", subject_id=child.id
            )
        )
        == 1
    )


async def test_dependent_availability_other_owner_404_child_403(
    client: AsyncClient,
    make_user: MakeUser,
    make_dependent: MakeDependent,
    principal_token: PrincipalToken,
    db_session: AsyncSession,
) -> None:
    parent = await make_user()
    stranger = await make_user()
    child_profile = await make_dependent(parent=parent, with_login=True)

    other = await client.put(
        dependent_url(child_profile.id),
        json=week(timezone="UTC"),
        headers=bearer(principal_token(stranger)),
    )
    assert (other.status_code, code(other)) == (404, "NOT_FOUND")

    child_user = await db_session.get(User, child_profile.login_user_id)
    assert child_user is not None
    as_child = await client.put(
        dependent_url(child_profile.id),
        json=week(),
        headers=bearer(principal_token(child_user, child=True)),
    )
    assert (as_child.status_code, code(as_child)) == (403, "FORBIDDEN")


async def test_dependent_availability_differing_timezone_422_parent_profile_untouched(
    client: AsyncClient,
    make_user: MakeUser,
    make_dependent: MakeDependent,
    principal_token: PrincipalToken,
    db_session: AsyncSession,
) -> None:
    parent = await make_user()
    child = await make_dependent(parent=parent)
    headers = bearer(principal_token(parent))

    r = await client.put(
        dependent_url(child.id),
        json=week((1, "09:00", "10:00"), timezone="Asia/Tokyo"),
        headers=headers,
    )

    assert (r.status_code, code(r)) == (422, "VALIDATION_ERROR")
    assert r.json()["error"]["details"][0]["field"] == "timezone"
    profile = await db_session.get(Profile, parent.id)
    assert profile is not None
    await db_session.refresh(profile)
    assert profile.timezone == "UTC"
    assert (await client.get("/api/v1/me", headers=headers)).json()["timezone"] == "UTC"
    assert (
        await AvailabilityRepository(db_session).get_by_subject(
            subject_type="player", subject_id=child.id
        )
        == []
    )


async def test_child_login_reads_owners_timezone_after_parent_write(
    client: AsyncClient,
    make_user: MakeUser,
    make_dependent: MakeDependent,
    principal_token: PrincipalToken,
    db_session: AsyncSession,
) -> None:
    parent = await make_user()
    parent_profile = await db_session.get(Profile, parent.id)
    assert parent_profile is not None
    parent_profile.timezone = "Europe/Paris"
    await db_session.flush()
    child = await make_dependent(parent=parent, with_login=True)

    put = await client.put(
        dependent_url(child.id),
        json=week((2, "09:00", "10:00"), timezone="Europe/Paris"),
        headers=bearer(principal_token(parent)),
    )
    assert put.status_code == 200
    child_user = await db_session.get(User, child.login_user_id)
    assert child_user is not None
    child_headers = bearer(principal_token(child_user, child=True))

    got = await client.get(AVAILABILITY, headers=child_headers)
    assert got.json() == {
        "timezone": "Europe/Paris",
        "windows": [{"weekday": 2, "start_time": "09:00", "end_time": "10:00"}],
    }
    own_write = await client.put(
        AVAILABILITY, json=week((3, "10:00", "11:00"), timezone="Asia/Tokyo"), headers=child_headers
    )
    assert (own_write.status_code, code(own_write)) == (422, "VALIDATION_ERROR")
    ok = await client.put(
        AVAILABILITY,
        json=week((3, "10:00", "11:00"), timezone="Europe/Paris"),
        headers=child_headers,
    )
    assert ok.status_code == 200
    await db_session.refresh(parent_profile)
    assert parent_profile.timezone == "Europe/Paris"
