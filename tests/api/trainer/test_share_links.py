from datetime import timedelta
from typing import Any

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.identity import ShareLink, UserRole
from app.repositories.coach_profile import CoachProfileRepository
from tests.api.helpers import bearer
from tests.factories import AddShareLink, MakeCoach, MakeUser, PrincipalToken
from tests.fakes import FakeClock

LINKS = "/api/v1/trainer/share-links"


def code(response: Any) -> str:
    return str(response.json()["error"]["code"])


async def test_trainer_creates_static_and_coach_unique_links(
    client: AsyncClient, make_trainer: MakeUser, principal_token: PrincipalToken, clock: FakeClock
) -> None:
    trainer = await make_trainer()
    headers = bearer(principal_token(trainer))

    static = await client.post(LINKS, json={"kind": "player_static"}, headers=headers)
    assert static.status_code == 201
    body = static.json()
    assert body["kind"] == "player_static"
    assert body["target_email"] is None
    assert (body["expires_at"], body["max_uses"], body["use_count"]) == (None, None, 0)
    assert body["is_active"] is True
    assert body["url"] == f"https://app.example.com/join/{body['code']}"
    assert body["created_at"].startswith("2026-10-18T10:00:00")

    unique = await client.post(
        LINKS,
        json={"kind": "coach_unique", "target_email": "sam@example.com"},
        headers=headers,
    )
    assert unique.status_code == 201
    body = unique.json()
    assert body["target_email"] == "sam@example.com"
    assert body["max_uses"] == 1
    assert body["expires_at"].startswith((clock.now() + timedelta(days=7)).strftime("%Y-%m-%dT%H"))
    assert body["code"] != static.json()["code"]


async def test_coach_creates_static_only_coach_unique_403(
    client: AsyncClient,
    make_trainer: MakeUser,
    make_coach: MakeCoach,
    principal_token: PrincipalToken,
) -> None:
    trainer = await make_trainer()
    coach = await make_coach(trainer)
    headers = bearer(principal_token(coach.user, trainer_id=trainer.id))

    ok = await client.post(LINKS, json={"kind": "player_static"}, headers=headers)
    assert ok.status_code == 201

    denied = await client.post(
        LINKS, json={"kind": "coach_unique", "target_email": "x@example.com"}, headers=headers
    )
    assert (denied.status_code, code(denied)) == (403, "FORBIDDEN")


async def test_target_email_required_iff_coach_unique_422(
    client: AsyncClient, make_trainer: MakeUser, principal_token: PrincipalToken
) -> None:
    headers = bearer(principal_token(await make_trainer()))
    for bad in (
        {"kind": "coach_unique"},
        {"kind": "player_static", "target_email": "a@example.com"},
        {"kind": "coach_unique", "target_email": "not-an-email"},
        {"kind": "other"},
        {"kind": "player_static", "trainer_id": "x"},
    ):
        r = await client.post(LINKS, json=bad, headers=headers)
        assert (r.status_code, code(r)) == (422, "VALIDATION_ERROR"), bad


async def test_coach_unique_for_active_coach_elsewhere_409_names_trainer(
    client: AsyncClient,
    make_trainer: MakeUser,
    make_coach: MakeCoach,
    principal_token: PrincipalToken,
) -> None:
    other = await make_trainer()
    busy = await make_coach(other)
    me = await make_trainer()

    r = await client.post(
        LINKS,
        json={"kind": "coach_unique", "target_email": busy.user.email},
        headers=bearer(principal_token(me)),
    )
    assert (r.status_code, code(r)) == (409, "COACH_ALREADY_ASSIGNED")
    assert r.json()["error"]["details"] == [{"current_trainer_id": str(other.id)}]


async def test_coach_unique_for_unknown_or_former_coach_is_allowed(
    client: AsyncClient,
    make_trainer: MakeUser,
    make_coach: MakeCoach,
    principal_token: PrincipalToken,
    db_session: AsyncSession,
    clock: FakeClock,
) -> None:
    other = await make_trainer()
    former = await make_coach(other)
    await CoachProfileRepository(db_session).close_tenure(
        trainer_id=other.id, user_id=former.user_id, now=clock.now()
    )
    me = await make_trainer()
    headers = bearer(principal_token(me))
    for email in (former.user.email, "brand-new@example.com"):
        r = await client.post(
            LINKS, json={"kind": "coach_unique", "target_email": email}, headers=headers
        )
        assert r.status_code == 201, email


async def test_list_links_filters_and_cursor_pagination_own_tenant_only(
    client: AsyncClient,
    make_trainer: MakeUser,
    add_share_link: AddShareLink,
    principal_token: PrincipalToken,
) -> None:
    me = await make_trainer()
    other = await make_trainer()
    mine = [await add_share_link(me) for _ in range(3)]
    unique = await add_share_link(me, kind="coach_unique", target_email="c@example.com")
    inactive = await add_share_link(me)
    inactive.is_active = False
    await add_share_link(other)
    headers = bearer(principal_token(me))

    seen: list[str] = []
    cursor: str | None = None
    while True:
        params: dict[str, Any] = {"limit": 2}
        if cursor:
            params["cursor"] = cursor
        page = (await client.get(LINKS, params=params, headers=headers)).json()
        assert len(page["items"]) <= 2
        seen += [item["id"] for item in page["items"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    expected = {str(x.id) for x in (*mine, unique, inactive)}
    assert sorted(seen) == sorted(expected)  # complete, no duplicates, own tenant only

    by_kind = (await client.get(LINKS, params={"kind": "coach_unique"}, headers=headers)).json()
    assert [i["id"] for i in by_kind["items"]] == [str(unique.id)]
    active = (await client.get(LINKS, params={"is_active": "false"}, headers=headers)).json()
    assert [i["id"] for i in active["items"]] == [str(inactive.id)]
    on = (
        await client.get(LINKS, params={"is_active": "true", "limit": 100}, headers=headers)
    ).json()
    assert str(inactive.id) not in {i["id"] for i in on["items"]}
    assert on["next_cursor"] is None


async def test_list_links_rejects_bad_params(
    client: AsyncClient, make_trainer: MakeUser, principal_token: PrincipalToken
) -> None:
    headers = bearer(principal_token(await make_trainer()))
    for params in ({"limit": 0}, {"limit": 101}, {"kind": "x"}, {"cursor": "garbage"}):
        r = await client.get(LINKS, params=params, headers=headers)
        assert (r.status_code, code(r)) == (422, "VALIDATION_ERROR"), params


async def test_delete_link_soft_deactivates_idempotent_other_tenant_404(
    client: AsyncClient,
    make_trainer: MakeUser,
    add_share_link: AddShareLink,
    principal_token: PrincipalToken,
    db_session: AsyncSession,
) -> None:
    me = await make_trainer()
    other = await make_trainer()
    link = await add_share_link(me)
    foreign = await add_share_link(other)
    headers = bearer(principal_token(me))

    for _ in range(2):  # idempotent
        r = await client.delete(f"{LINKS}/{link.id}", headers=headers)
        assert r.status_code == 204
    row = await db_session.get(ShareLink, link.id, populate_existing=True)
    assert row is not None and row.is_active is False

    r = await client.delete(f"{LINKS}/{foreign.id}", headers=headers)
    assert (r.status_code, code(r)) == (404, "NOT_FOUND")
    row = await db_session.get(ShareLink, foreign.id, populate_existing=True)
    assert row is not None and row.is_active is True
    unknown = await client.delete(f"{LINKS}/00000000-0000-7000-8000-000000000000", headers=headers)
    assert unknown.status_code == 404


async def test_delete_link_is_trainer_only_and_roles_gate_every_route(
    client: AsyncClient,
    make_trainer: MakeUser,
    make_user: MakeUser,
    make_coach: MakeCoach,
    add_share_link: AddShareLink,
    principal_token: PrincipalToken,
) -> None:
    trainer = await make_trainer()
    link = await add_share_link(trainer)
    coach = await make_coach(trainer)
    coach_headers = bearer(principal_token(coach.user, trainer_id=trainer.id))
    assert (await client.get(LINKS, headers=coach_headers)).status_code == 200
    assert (await client.delete(f"{LINKS}/{link.id}", headers=coach_headers)).status_code == 403

    for role in (UserRole.player, UserRole.super_admin):
        user = await make_user(role=role)
        headers = bearer(principal_token(user))
        assert (await client.get(LINKS, headers=headers)).status_code == 403
        assert (
            await client.post(LINKS, json={"kind": "player_static"}, headers=headers)
        ).status_code == 403
    # a player cannot borrow the tenant through ?trainer_id
    player = await make_user(role=UserRole.player)
    r = await client.get(
        LINKS, params={"trainer_id": str(trainer.id)}, headers=bearer(principal_token(player))
    )
    assert r.status_code == 403
    assert (await client.get(LINKS)).status_code == 401
