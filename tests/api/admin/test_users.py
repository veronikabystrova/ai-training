from typing import Any

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.identity import Profile, UserRole, UserStatus
from app.models.platform import AuditLog
from tests.api.helpers import bearer
from tests.factories import MakeCoach, MakeUser

USERS = "/api/v1/admin/users"


def code(response: Any) -> str:
    return str(response.json()["error"]["code"])


async def all_pages(client: AsyncClient, token: str, **params: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        query = {**params, "limit": 2}
        if cursor:
            query["cursor"] = cursor
        r = await client.get(USERS, params=query, headers=bearer(token))
        assert r.status_code == 200, r.text
        body = r.json()
        assert len(body["items"]) <= 2
        items += body["items"]
        cursor = body["next_cursor"]
        if cursor is None:
            return items


async def test_directory_filters_keyset_order_and_trgm_query(
    client: AsyncClient,
    admin_token: str,
    make_user: MakeUser,
    make_trainer: MakeUser,
    db_session: AsyncSession,
) -> None:
    players = [await make_user(first_name=f"Pat{i}") for i in range(5)]
    zoe = await make_user(email="zoe.smith@example.com", first_name="Q", last_name="R")
    zoey = await make_user(first_name="Zoey", last_name="Quinn")
    inactive = await make_user(status=UserStatus.inactive)
    trainer = await make_trainer(first_name="Tina")

    # keyset: newest first, every row once
    seen = await all_pages(client, admin_token, role="player")
    ids = [i["id"] for i in seen]
    expected = {str(u.id) for u in (*players, zoe, zoey, inactive)}
    assert set(ids) == expected and len(ids) == len(expected)
    assert ids == sorted(ids, reverse=True)  # one transaction: created_at ties, id decides

    summary = next(i for i in seen if i["id"] == str(zoe.id))
    assert set(summary) == {
        "id",
        "email",
        "role",
        "status",
        "first_name",
        "last_name",
        "trainer_id",
        "created_at",
        "last_login_at",
    }
    assert summary["trainer_id"] is None and summary["last_login_at"] is None

    by_status = await all_pages(client, admin_token, status="inactive")
    assert [i["id"] for i in by_status] == [str(inactive.id)]

    by_query = {i["id"] for i in await all_pages(client, admin_token, query="zoe")}
    assert by_query == {str(zoe.id), str(zoey.id)}  # email match and full-name match
    by_name = {i["id"] for i in await all_pages(client, admin_token, query="quinn")}
    assert by_name == {str(zoey.id)}
    assert await all_pages(client, admin_token, query="%%") == []  # wildcards are escaped

    trainers = await all_pages(client, admin_token, role="trainer")
    assert [(i["id"], i["trainer_id"]) for i in trainers] == [(str(trainer.id), str(trainer.id))]
    profile = await db_session.get(Profile, zoe.id)
    assert profile is not None


async def test_directory_trainer_id_filter_returns_the_trainer_and_its_active_coaches(
    client: AsyncClient,
    admin_token: str,
    make_trainer: MakeUser,
    make_coach: MakeCoach,
    make_user: MakeUser,
) -> None:
    trainer, other = await make_trainer(), await make_trainer()
    coach = await make_coach(trainer)
    await make_coach(other)
    await make_user()

    rows = await all_pages(client, admin_token, trainer_id=str(trainer.id))
    assert {(r["id"], r["role"], r["trainer_id"]) for r in rows} == {
        (str(trainer.id), "trainer", str(trainer.id)),
        (str(coach.user_id), "coach", str(trainer.id)),
    }


async def test_directory_rejects_bad_params(client: AsyncClient, admin_token: str) -> None:
    for params in (
        {"query": "a"},
        {"query": "  a "},
        {"limit": 0},
        {"limit": 101},
        {"role": "boss"},
        {"status": "gone"},
        {"trainer_id": "nope"},
        {"cursor": "garbage"},
    ):
        r = await client.get(USERS, params=params, headers=bearer(admin_token))
        assert (r.status_code, code(r)) == (422, "VALIDATION_ERROR"), params


async def test_directory_query_min_2_chars_422(client: AsyncClient, admin_token: str) -> None:
    r = await client.get(USERS, params={"query": "x"}, headers=bearer(admin_token))
    assert (r.status_code, code(r)) == (422, "VALIDATION_ERROR")
    ok = await client.get(USERS, params={"query": "xy"}, headers=bearer(admin_token))
    assert ok.status_code == 200


async def test_get_patch_user_and_immutable_role_email_422(
    client: AsyncClient, admin_token: str, make_user: MakeUser, db_session: AsyncSession
) -> None:
    user = await make_user(first_name="Old", last_name="Name")
    headers = bearer(admin_token)

    r = await client.get(f"{USERS}/{user.id}", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == str(user.id) and body["email"] == user.email
    assert body["timezone"] == "UTC" and body["email_verified"] is True
    assert body["must_change_password"] is False and body["deleted_at"] is None
    assert body["business_name"] is None and body["slug"] is None and body["phone"] is None

    r = await client.patch(
        f"{USERS}/{user.id}",
        json={"first_name": "New", "phone": "+33123", "timezone": "Europe/Paris"},
        headers=headers,
    )
    assert r.status_code == 200
    assert (r.json()["first_name"], r.json()["last_name"]) == ("New", "Name")
    assert (r.json()["phone"], r.json()["timezone"]) == ("+33123", "Europe/Paris")
    cleared = await client.patch(f"{USERS}/{user.id}", json={"phone": None}, headers=headers)
    assert cleared.json()["phone"] is None

    for bad in (
        {"role": "trainer"},
        {"email": "other@example.com"},
        {"status": "active"},
        {"first_name": ""},
        {"first_name": None},
        {"timezone": None},
        {"timezone": "Mars/Base"},
        {"phone": "9" * 33},
    ):
        r = await client.patch(f"{USERS}/{user.id}", json=bad, headers=headers)
        assert (r.status_code, code(r)) == (422, "VALIDATION_ERROR"), bad

    unknown = "00000000-0000-7000-8000-000000000000"
    assert (await client.get(f"{USERS}/{unknown}", headers=headers)).status_code == 404
    assert (await client.patch(f"{USERS}/{unknown}", json={}, headers=headers)).status_code == 404


async def test_get_trainer_detail_has_business_name_and_slug(
    client: AsyncClient, admin_token: str, make_trainer: MakeUser
) -> None:
    trainer = await make_trainer()
    r = await client.get(f"{USERS}/{trainer.id}", headers=bearer(admin_token))
    body = r.json()
    assert body["role"] == "trainer" and body["trainer_id"] == str(trainer.id)
    assert body["business_name"].startswith("Academy") and body["slug"].startswith("academy-")


async def test_patch_records_audit_without_values(
    client: AsyncClient,
    admin_token: str,
    admin: Any,
    make_user: MakeUser,
    db_session: AsyncSession,
) -> None:
    user = await make_user()
    await client.patch(
        f"{USERS}/{user.id}",
        json={"first_name": "Zed", "phone": "123"},
        headers=bearer(admin_token),
    )
    row = (
        await db_session.execute(select(AuditLog).where(AuditLog.action == "user.updated"))
    ).scalar_one()
    assert row.actor_user_id == admin.id and row.subject_id == user.id
    assert row.metadata_ == {"fields": ["first_name", "phone"]}


async def test_every_admin_route_non_admin_403_anonymous_401(
    client: AsyncClient,
    make_user: MakeUser,
    make_trainer: MakeUser,
    make_coach: MakeCoach,
    principal_token: Any,
) -> None:
    trainer = await make_trainer()
    coach = await make_coach(trainer)
    player = await make_user(role=UserRole.player)
    tokens = {
        "trainer": principal_token(trainer),
        "coach": principal_token(coach.user, trainer_id=trainer.id),
        "player": principal_token(player),
        "child": principal_token(player, child=True),
    }
    target = str(player.id)
    provision = {
        "email": "n@example.com",
        "first_name": "N",
        "last_name": "T",
        "business_name": "B",
        "slug": "bbb",
    }
    routes: list[tuple[str, str, dict[str, Any] | None]] = [
        ("GET", USERS, None),
        ("POST", f"{USERS}/trainers", provision),
        ("GET", f"{USERS}/{target}", None),
        ("PATCH", f"{USERS}/{target}", {"first_name": "X"}),
        ("POST", f"{USERS}/{target}/deactivate", None),
        ("POST", f"{USERS}/{target}/reactivate", None),
        ("DELETE", f"{USERS}/{target}", None),
        ("POST", f"{USERS}/{target}/impersonate", None),
    ]
    for method, path, body in routes:
        anonymous = await client.request(method, path, json=body)
        assert (anonymous.status_code, code(anonymous)) == (401, "INVALID_TOKEN"), (method, path)
        for who, token in tokens.items():
            r = await client.request(method, path, json=body, headers=bearer(token))
            assert (r.status_code, code(r)) == (403, "FORBIDDEN"), (method, path, who)
