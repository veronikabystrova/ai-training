from typing import Any
from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.identity import Profile
from tests.api.helpers import bearer
from tests.factories import MakeUser, PrincipalToken
from tests.fakes import FakeObjectStorage

AVATAR = "/api/v1/me/avatar"
CONFIRM = "/api/v1/me/avatar/confirm"
FIVE_MB = 5 * 1024 * 1024


def code(response: Any) -> str:
    return str(response.json()["error"]["code"])


async def test_avatar_presign_constraints_and_server_chosen_key(
    client: AsyncClient, make_user: MakeUser, principal_token: PrincipalToken
) -> None:
    headers = bearer(principal_token(await make_user()))
    r = await client.put(
        AVATAR, json={"content_type": "image/png", "content_length": 1000}, headers=headers
    )
    assert r.status_code == 200
    body = r.json()
    assert body["key"].startswith("avatars/")
    assert body["upload_url"].endswith(body["key"])
    assert body["expires_in"] == 300
    assert body["required_headers"] == {"Content-Type": "image/png"}

    again = await client.put(
        AVATAR, json={"content_type": "image/png", "content_length": 1000}, headers=headers
    )
    assert again.json()["key"] != body["key"]


async def test_avatar_presign_rejects_type_size_and_extra_fields(
    client: AsyncClient, make_user: MakeUser, principal_token: PrincipalToken
) -> None:
    headers = bearer(principal_token(await make_user()))
    ok = {"content_type": "image/webp", "content_length": FIVE_MB}
    assert (await client.put(AVATAR, json=ok, headers=headers)).status_code == 200
    for bad in (
        {"content_type": "image/gif", "content_length": 1000},
        {"content_type": "image/png", "content_length": FIVE_MB + 1},
        {"content_type": "image/png", "content_length": 0},
        {**ok, "key": "avatars/chosen-by-client"},
    ):
        r = await client.put(AVATAR, json=bad, headers=headers)
        assert (r.status_code, code(r)) == (422, "VALIDATION_ERROR"), bad


async def test_avatar_confirm_head_checks_then_persists(
    client: AsyncClient,
    make_user: MakeUser,
    principal_token: PrincipalToken,
    storage: FakeObjectStorage,
    db_session: AsyncSession,
) -> None:
    user = await make_user()
    headers = bearer(principal_token(user))
    key = f"avatars/{uuid4()}"
    await storage.put(key, b"x" * 100, "image/jpeg")

    r = await client.post(CONFIRM, json={"avatar_key": key}, headers=headers)
    assert r.status_code == 200
    assert r.json()["avatar_url"] == f"https://media.example.com/{key}"
    profile = await db_session.get(Profile, user.id)
    assert profile is not None
    assert profile.avatar_key == key
    me = await client.get("/api/v1/me", headers=headers)
    assert me.json()["avatar_url"] == f"https://media.example.com/{key}"


async def test_avatar_confirm_missing_key_404_wrong_type_or_size_invalid_avatar(
    client: AsyncClient,
    make_user: MakeUser,
    principal_token: PrincipalToken,
    storage: FakeObjectStorage,
    db_session: AsyncSession,
) -> None:
    user = await make_user()
    headers = bearer(principal_token(user))

    missing = await client.post(CONFIRM, json={"avatar_key": f"avatars/{uuid4()}"}, headers=headers)
    assert (missing.status_code, code(missing)) == (404, "NOT_FOUND")

    gif = f"avatars/{uuid4()}"
    await storage.put(gif, b"x", "image/gif")
    big = f"avatars/{uuid4()}"
    await storage.put(big, b"x" * (FIVE_MB + 1), "image/png")
    for key in (gif, big):
        r = await client.post(CONFIRM, json={"avatar_key": key}, headers=headers)
        assert (r.status_code, code(r)) == (422, "INVALID_AVATAR")
    profile = await db_session.get(Profile, user.id)
    assert profile is not None
    assert profile.avatar_key is None


async def test_avatar_confirm_rejects_keys_outside_the_avatar_namespace(
    client: AsyncClient, make_user: MakeUser, principal_token: PrincipalToken
) -> None:
    headers = bearer(principal_token(await make_user()))
    for key in ("logos/" + str(uuid4()), "avatars/../secret", "avatars/short"):
        r = await client.post(CONFIRM, json={"avatar_key": key}, headers=headers)
        assert (r.status_code, code(r)) == (422, "VALIDATION_ERROR"), key


async def test_avatar_routes_require_authentication(client: AsyncClient) -> None:
    assert (
        await client.put(AVATAR, json={"content_type": "image/png", "content_length": 1})
    ).status_code == 401
    assert (
        await client.post(CONFIRM, json={"avatar_key": f"avatars/{uuid4()}"})
    ).status_code == 401
