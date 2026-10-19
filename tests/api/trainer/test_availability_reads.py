from typing import Any

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.identity import Profile, UserRole
from app.repositories.coach_profile import CoachProfileRepository
from tests.api.helpers import bearer
from tests.factories import (
    AddWindow,
    LinkPlayer,
    MakeCoach,
    MakeDependent,
    MakePlayer,
    MakeUser,
    PrincipalToken,
)
from tests.fakes import FakeClock

PLAYERS = "/api/v1/trainer/players"
COACHES = "/api/v1/trainer/coaches"


def code(response: Any) -> str:
    return str(response.json()["error"]["code"])


async def test_trainer_reads_linked_player_availability_else_404(
    client: AsyncClient,
    make_trainer: MakeUser,
    make_player: MakePlayer,
    link_player: LinkPlayer,
    add_window: AddWindow,
    principal_token: PrincipalToken,
    db_session: AsyncSession,
) -> None:
    trainer = await make_trainer()
    player = await make_player()
    profile = await db_session.get(Profile, player.owner_user_id)
    assert profile is not None
    profile.timezone = "Europe/Paris"
    link = await link_player(trainer, player)
    await add_window(player.id, weekday=2, start="16:00", end="18:00")
    await add_window(player.id, weekday=0, start="09:00", end="10:00")
    headers = bearer(principal_token(trainer))

    r = await client.get(f"{PLAYERS}/{player.id}/availability", headers=headers)
    assert r.status_code == 200
    assert r.json() == {
        "subject_id": str(player.id),
        "subject_type": "player",
        "timezone": "Europe/Paris",
        "windows": [
            {"weekday": 0, "start_time": "09:00", "end_time": "10:00"},
            {"weekday": 2, "start_time": "16:00", "end_time": "18:00"},
        ],
    }

    link.status = link.status.inactive  # inactive link => 404
    await db_session.flush()
    gone = await client.get(f"{PLAYERS}/{player.id}/availability", headers=headers)
    assert (gone.status_code, code(gone)) == (404, "NOT_FOUND")


async def test_trainer_read_of_unlinked_other_tenant_or_unknown_player_404(
    client: AsyncClient,
    make_trainer: MakeUser,
    make_player: MakePlayer,
    link_player: LinkPlayer,
    principal_token: PrincipalToken,
) -> None:
    me = await make_trainer()
    other = await make_trainer()
    theirs = await make_player()
    await link_player(other, theirs)
    unlinked = await make_player()
    headers = bearer(principal_token(me))
    for pid in (theirs.id, unlinked.id, "00000000-0000-7000-8000-000000000000"):
        r = await client.get(f"{PLAYERS}/{pid}/availability", headers=headers)
        assert (r.status_code, code(r)) == (404, "NOT_FOUND"), pid


async def test_dependent_availability_uses_the_parents_timezone_and_coach_can_read(
    client: AsyncClient,
    make_trainer: MakeUser,
    make_user: MakeUser,
    make_coach: MakeCoach,
    make_dependent: MakeDependent,
    link_player: LinkPlayer,
    add_window: AddWindow,
    principal_token: PrincipalToken,
    db_session: AsyncSession,
) -> None:
    trainer = await make_trainer()
    parent = await make_user(role=UserRole.player)
    parent_profile = await db_session.get(Profile, parent.id)
    assert parent_profile is not None
    parent_profile.timezone = "America/New_York"
    child = await make_dependent(parent=parent)
    await link_player(trainer, child)
    await add_window(child.id, weekday=5, start="10:00", end="11:00")
    coach = await make_coach(trainer)

    r = await client.get(
        f"{PLAYERS}/{child.id}/availability",
        headers=bearer(principal_token(coach.user, trainer_id=trainer.id)),
    )
    assert r.status_code == 200
    assert r.json()["timezone"] == "America/New_York"
    assert r.json()["subject_type"] == "player"


async def test_trainer_reads_active_coach_availability_former_coach_404_coach_role_403(
    client: AsyncClient,
    make_trainer: MakeUser,
    make_coach: MakeCoach,
    add_window: AddWindow,
    principal_token: PrincipalToken,
    db_session: AsyncSession,
    clock: FakeClock,
) -> None:
    trainer = await make_trainer()
    other = await make_trainer()
    coach = await make_coach(trainer)
    foreign = await make_coach(other)
    await add_window(coach.user_id, weekday=1, start="08:00", end="12:00", subject_type="coach")
    headers = bearer(principal_token(trainer))

    r = await client.get(f"{COACHES}/{coach.user_id}/availability", headers=headers)
    assert r.status_code == 200
    assert r.json() == {
        "subject_id": str(coach.user_id),
        "subject_type": "coach",
        "timezone": "UTC",
        "windows": [{"weekday": 1, "start_time": "08:00", "end_time": "12:00"}],
    }

    other_tenant = await client.get(f"{COACHES}/{foreign.user_id}/availability", headers=headers)
    assert (other_tenant.status_code, code(other_tenant)) == (404, "NOT_FOUND")

    as_coach = await client.get(
        f"{COACHES}/{coach.user_id}/availability",
        headers=bearer(principal_token(coach.user, trainer_id=trainer.id)),
    )
    assert (as_coach.status_code, code(as_coach)) == (403, "FORBIDDEN")

    await CoachProfileRepository(db_session).close_tenure(
        trainer_id=trainer.id, user_id=coach.user_id, now=clock.now()
    )
    former = await client.get(f"{COACHES}/{coach.user_id}/availability", headers=headers)
    assert (former.status_code, code(former)) == (404, "NOT_FOUND")


async def test_availability_reads_deny_players_and_anonymous(
    client: AsyncClient,
    make_user: MakeUser,
    make_player: MakePlayer,
    principal_token: PrincipalToken,
) -> None:
    player = await make_player()
    pid = player.id
    owner = await make_user(role=UserRole.player)
    headers = bearer(principal_token(owner))
    assert (await client.get(f"{PLAYERS}/{pid}/availability", headers=headers)).status_code == 403
    assert (await client.get(f"{COACHES}/{pid}/availability", headers=headers)).status_code == 403
    assert (await client.get(f"{PLAYERS}/{pid}/availability")).status_code == 401
