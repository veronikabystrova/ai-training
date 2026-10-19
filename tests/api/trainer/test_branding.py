from typing import Any
from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.identity import TrainerProfile, UserRole
from tests.api.helpers import bearer
from tests.factories import MakeCoach, MakeUser, PrincipalToken
from tests.fakes import FakeJobQueue, FakeObjectStorage

BRANDING = "/api/v1/trainer/branding"
LOGO = f"{BRANDING}/logo"
CONFIRM = f"{LOGO}/confirm"
TWO_MB = 2 * 1024 * 1024
CLEAN_SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect/></svg>'


def code(response: Any) -> str:
    return str(response.json()["error"]["code"])


async def issued_key(client: AsyncClient, headers: dict[str, str], kind: str = "image/png") -> str:
    r = await client.post(
        LOGO, json={"content_type": kind, "content_length": 1000}, headers=headers
    )
    assert r.status_code == 200, r.text
    return str(r.json()["key"])


async def test_branding_get_for_trainer_and_coach_patch_trainer_only(
    client: AsyncClient,
    make_trainer: MakeUser,
    make_coach: MakeCoach,
    principal_token: PrincipalToken,
    db_session: AsyncSession,
) -> None:
    trainer = await make_trainer()
    coach = await make_coach(trainer)
    profile = await db_session.get(TrainerProfile, trainer.id)
    assert profile is not None
    profile.business_name = "Acme Padel"
    profile.brand_primary_color = "#112233"
    trainer_headers = bearer(principal_token(trainer))
    coach_headers = bearer(principal_token(coach.user, trainer_id=trainer.id))

    for headers in (trainer_headers, coach_headers):
        r = await client.get(BRANDING, headers=headers)
        assert r.status_code == 200
        assert r.json() == {
            "business_name": "Acme Padel",
            "logo_url": None,
            "primary_color": "#112233",
            "secondary_color": None,
        }

    r = await client.patch(
        BRANDING,
        json={"business_name": "Acme Tennis", "secondary_color": "#AABBCC"},
        headers=trainer_headers,
    )
    assert r.status_code == 200
    assert r.json()["business_name"] == "Acme Tennis"
    assert r.json()["primary_color"] == "#112233"  # untouched
    assert r.json()["secondary_color"] == "#AABBCC"

    cleared = await client.patch(BRANDING, json={"primary_color": None}, headers=trainer_headers)
    assert cleared.json()["primary_color"] is None

    denied = await client.patch(BRANDING, json={"business_name": "X"}, headers=coach_headers)
    assert (denied.status_code, code(denied)) == (403, "FORBIDDEN")
    assert (await client.get(BRANDING)).status_code == 401


async def test_branding_is_denied_to_players_and_admins(
    client: AsyncClient, make_user: MakeUser, principal_token: PrincipalToken
) -> None:
    for role in (UserRole.player, UserRole.super_admin):
        headers = bearer(principal_token(await make_user(role=role)))
        assert (await client.get(BRANDING, headers=headers)).status_code == 403
        assert (await client.patch(BRANDING, json={}, headers=headers)).status_code == 403


async def test_patch_branding_hex_regex_422(
    client: AsyncClient, make_trainer: MakeUser, principal_token: PrincipalToken
) -> None:
    headers = bearer(principal_token(await make_trainer()))
    for bad in (
        {"primary_color": "red"},
        {"primary_color": "#12345"},
        {"secondary_color": "#GGGGGG"},
        {"secondary_color": "123456"},
        {"business_name": ""},
        {"business_name": None},
        {"business_name": "x" * 121},
        {"logo_key": "logos/x"},
    ):
        r = await client.patch(BRANDING, json=bad, headers=headers)
        assert (r.status_code, code(r)) == (422, "VALIDATION_ERROR"), bad


async def test_logo_presign_type_and_2mb_limit(
    client: AsyncClient, make_trainer: MakeUser, principal_token: PrincipalToken
) -> None:
    headers = bearer(principal_token(await make_trainer()))
    for kind in ("image/png", "image/svg+xml", "image/webp"):
        r = await client.post(
            LOGO, json={"content_type": kind, "content_length": TWO_MB}, headers=headers
        )
        assert r.status_code == 200
        body = r.json()
        assert body["key"].startswith("logos/")
        assert len(body["key"]) == len("logos/") + 36
        assert body["upload_url"].endswith(body["key"])
        assert body["required_headers"] == {"Content-Type": kind}
        assert body["expires_in"] == 300
    for bad in (
        {"content_type": "image/jpeg", "content_length": 100},
        {"content_type": "image/png", "content_length": TWO_MB + 1},
        {"content_type": "image/png", "content_length": 0},
        {"content_type": "image/png", "content_length": 100, "key": "logos/mine"},
    ):
        r = await client.post(LOGO, json=bad, headers=headers)
        assert (r.status_code, code(r)) == (422, "VALIDATION_ERROR"), bad


async def test_logo_confirm_png_persists_key(
    client: AsyncClient,
    make_trainer: MakeUser,
    principal_token: PrincipalToken,
    storage: FakeObjectStorage,
    db_session: AsyncSession,
) -> None:
    trainer = await make_trainer()
    headers = bearer(principal_token(trainer))
    key = await issued_key(client, headers)
    await storage.put(key, b"\x89PNG....", "image/png")

    r = await client.post(CONFIRM, json={"logo_key": key}, headers=headers)
    assert r.status_code == 200
    assert r.json()["logo_url"] == f"https://media.example.com/{key}"
    profile = await db_session.get(TrainerProfile, trainer.id)
    assert profile is not None
    assert profile.logo_key == key
    assert storage.objects[key][0] == b"\x89PNG...."  # raster images are not rewritten


async def test_logo_confirm_svg_is_sanitised_and_reserialised(
    client: AsyncClient,
    make_trainer: MakeUser,
    principal_token: PrincipalToken,
    storage: FakeObjectStorage,
    db_session: AsyncSession,
) -> None:
    trainer = await make_trainer()
    headers = bearer(principal_token(trainer))
    key = await issued_key(client, headers, "image/svg+xml")
    await storage.put(
        key,
        b'<svg xmlns="http://www.w3.org/2000/svg" onload="x()"><script/><rect/></svg>',
        "image/svg+xml",
    )

    r = await client.post(CONFIRM, json={"logo_key": key}, headers=headers)
    assert r.status_code == 200
    profile = await db_session.get(TrainerProfile, trainer.id)
    assert profile is not None and profile.logo_key is not None
    stored, content_type = storage.objects[profile.logo_key]
    assert content_type == "image/svg+xml"
    assert b"<script" not in stored and b"onload" not in stored
    assert b"<rect" in stored


async def test_logo_confirm_svg_persists_a_never_presigned_key_replay_cannot_replace_it(
    client: AsyncClient,
    make_trainer: MakeUser,
    principal_token: PrincipalToken,
    storage: FakeObjectStorage,
    db_session: AsyncSession,
) -> None:
    trainer = await make_trainer()
    headers = bearer(principal_token(trainer))
    key = await issued_key(client, headers, "image/svg+xml")
    await storage.put(key, CLEAN_SVG, "image/svg+xml")

    r = await client.post(CONFIRM, json={"logo_key": key}, headers=headers)
    assert r.status_code == 200
    profile = await db_session.get(TrainerProfile, trainer.id)
    assert profile is not None and profile.logo_key is not None
    served_key = profile.logo_key
    assert served_key != key and r.json()["logo_url"].endswith(served_key)
    before = storage.objects[served_key]

    # replaying the still-valid presigned PUT with hostile bytes must not touch the served copy
    await storage.put(key, b"<svg onload='x()'/>", "image/svg+xml")
    assert storage.objects[served_key] == before
    assert b"onload" not in storage.objects[served_key][0]


async def test_logo_confirm_hostile_svg_xxe_422_invalid_logo(
    client: AsyncClient,
    make_trainer: MakeUser,
    principal_token: PrincipalToken,
    storage: FakeObjectStorage,
    db_session: AsyncSession,
) -> None:
    trainer = await make_trainer()
    headers = bearer(principal_token(trainer))
    key = await issued_key(client, headers, "image/svg+xml")
    xxe = (
        b'<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
        b'<svg xmlns="http://www.w3.org/2000/svg"><title>&x;</title></svg>'
    )
    await storage.put(key, xxe, "image/svg+xml")

    r = await client.post(CONFIRM, json={"logo_key": key}, headers=headers)
    assert (r.status_code, code(r)) == (422, "INVALID_LOGO")
    profile = await db_session.get(TrainerProfile, trainer.id)
    assert profile is not None
    assert profile.logo_key is None


async def test_logo_confirm_wrong_stored_type_or_size_invalid_logo(
    client: AsyncClient,
    make_trainer: MakeUser,
    principal_token: PrincipalToken,
    storage: FakeObjectStorage,
) -> None:
    headers = bearer(principal_token(await make_trainer()))
    gif = await issued_key(client, headers)
    await storage.put(gif, b"x", "image/gif")
    big = await issued_key(client, headers)
    await storage.put(big, b"x" * (TWO_MB + 1), "image/png")
    for key in (gif, big):
        r = await client.post(CONFIRM, json={"logo_key": key}, headers=headers)
        assert (r.status_code, code(r)) == (422, "INVALID_LOGO")


async def test_logo_confirm_missing_key_404_coach_403_foreign_key_404(
    client: AsyncClient,
    make_trainer: MakeUser,
    make_coach: MakeCoach,
    principal_token: PrincipalToken,
    storage: FakeObjectStorage,
    db_session: AsyncSession,
) -> None:
    trainer, other = await make_trainer(), await make_trainer()
    coach = await make_coach(trainer)
    headers = bearer(principal_token(trainer))
    other_headers = bearer(principal_token(other))

    issued_not_uploaded = await issued_key(client, headers)
    r = await client.post(CONFIRM, json={"logo_key": issued_not_uploaded}, headers=headers)
    assert (r.status_code, code(r)) == (404, "NOT_FOUND")

    key = await issued_key(client, headers)
    await storage.put(key, b"x" * 10, "image/png")
    forged = f"logos/{uuid4()}"
    await storage.put(forged, b"x" * 10, "image/png")
    for hdrs, logo_key in ((other_headers, key), (headers, forged)):
        r = await client.post(CONFIRM, json={"logo_key": logo_key}, headers=hdrs)
        assert (r.status_code, code(r)) == (404, "NOT_FOUND")
    other_profile = await db_session.get(TrainerProfile, other.id)
    assert other_profile is not None
    assert other_profile.logo_key is None

    coach_headers = bearer(principal_token(coach.user, trainer_id=trainer.id))
    for method, path, body in (
        ("POST", LOGO, {"content_type": "image/png", "content_length": 10}),
        ("POST", CONFIRM, {"logo_key": key}),
    ):
        r = await client.request(method, path, json=body, headers=coach_headers)
        assert (r.status_code, code(r)) == (403, "FORBIDDEN")

    bad = await client.post(CONFIRM, json={"logo_key": "avatars/" + str(uuid4())}, headers=headers)
    assert (bad.status_code, code(bad)) == (422, "VALIDATION_ERROR")


async def test_logo_replace_enqueues_deletion_of_the_old_logo_only(
    client: AsyncClient,
    make_trainer: MakeUser,
    principal_token: PrincipalToken,
    storage: FakeObjectStorage,
    db_session: AsyncSession,
    jobs: FakeJobQueue,
) -> None:
    trainer = await make_trainer()
    headers = bearer(principal_token(trainer))
    first = await issued_key(client, headers)
    await storage.put(first, b"\x89PNG....", "image/png")
    assert (
        await client.post(CONFIRM, json={"logo_key": first}, headers=headers)
    ).status_code == 200
    assert jobs.enqueued == []  # no previous logo

    second = await issued_key(client, headers)
    await storage.put(second, b"\x89PNG....", "image/png")
    assert (
        await client.post(CONFIRM, json={"logo_key": second}, headers=headers)
    ).status_code == 200
    assert [(j.name, j.kwargs) for j in jobs.enqueued] == [
        ("delete_storage_object", {"key": first})
    ]

    jobs.enqueued.clear()  # re-confirming the live key must not delete it
    assert (
        await client.post(CONFIRM, json={"logo_key": second}, headers=headers)
    ).status_code == 200
    assert jobs.enqueued == []


async def test_logo_replace_with_svg_deletes_the_old_served_key(
    client: AsyncClient,
    make_trainer: MakeUser,
    principal_token: PrincipalToken,
    storage: FakeObjectStorage,
    db_session: AsyncSession,
    jobs: FakeJobQueue,
) -> None:
    trainer = await make_trainer()
    profile = await db_session.get(TrainerProfile, trainer.id)
    assert profile is not None
    profile.logo_key = "logos/old-served"
    await db_session.flush()
    headers = bearer(principal_token(trainer))
    key = await issued_key(client, headers, "image/svg+xml")
    await storage.put(key, CLEAN_SVG, "image/svg+xml")
    assert (await client.post(CONFIRM, json={"logo_key": key}, headers=headers)).status_code == 200
    assert [(j.name, j.kwargs) for j in jobs.enqueued] == [
        ("delete_storage_object", {"key": "logos/old-served"})
    ]
