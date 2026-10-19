import time
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.identity import CoachProfile, UserRole, UserStatus
from tests.api.helpers import bearer
from tests.factories import (
    MakeCoach,
    MakeTrainer,
    MakeUser,
    PrincipalToken,
)

ME = "/api/v1/me"


def code(response: Any) -> str:
    return str(response.json()["error"]["code"])


async def test_get_me_shapes_for_each_role(
    client: AsyncClient,
    make_user: MakeUser,
    make_trainer: MakeTrainer,
    make_coach: MakeCoach,
    principal_token: PrincipalToken,
) -> None:
    trainer = await make_trainer(first_name="Tina")
    tenure = await make_coach(trainer)
    player = await make_user(role=UserRole.player, email="p@x.com")
    admin = await make_user(role=UserRole.super_admin)

    r = await client.get(ME, headers=bearer(principal_token(player)))
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == str(player.id)
    assert body["email"] == "p@x.com"
    assert body["role"] == "player"
    assert body["email_verified"] is True
    assert body["coach"] is None
    assert body["trainer_id"] is None
    assert body["impersonated_by"] is None
    assert body["avatar_url"] is None
    assert body["timezone"] == "UTC"
    assert "password_hash" not in body

    r = await client.get(ME, headers=bearer(principal_token(trainer)))
    assert (r.json()["trainer_id"], r.json()["coach"], r.json()["first_name"]) == (
        str(trainer.id),
        None,
        "Tina",
    )

    r = await client.get(ME, headers=bearer(principal_token(admin)))
    assert (r.status_code, r.json()["coach"]) == (200, None)

    r = await client.get(ME, headers=bearer(principal_token(tenure.user, trainer_id=trainer.id)))
    assert r.json()["trainer_id"] == str(trainer.id)
    assert r.json()["coach"] == {
        "bio": None,
        "credentials": None,
        "certifications": [],
        "is_public": False,
    }


async def test_get_me_requires_authentication(client: AsyncClient) -> None:
    assert (await client.get(ME)).status_code == 401


async def test_get_me_404_when_coach_tenant_deactivated(
    client: AsyncClient,
    make_trainer: MakeTrainer,
    make_coach: MakeCoach,
    principal_token: PrincipalToken,
    db_session: AsyncSession,
) -> None:
    trainer = await make_trainer()
    tenure = await make_coach(trainer)
    token = principal_token(tenure.user, trainer_id=trainer.id)
    trainer.status = UserStatus.inactive
    await db_session.flush()
    r = await client.get(ME, headers=bearer(token))
    assert (r.status_code, code(r)) == (404, "NOT_FOUND")


async def test_get_me_works_while_must_change_password(
    client: AsyncClient,
    make_user: MakeUser,
    principal_token: PrincipalToken,
    db_session: AsyncSession,
) -> None:
    user = await make_user()
    user.must_change_password = True
    await db_session.flush()
    r = await client.get(ME, headers=bearer(principal_token(user)))
    assert (r.status_code, r.json()["must_change_password"]) == (200, True)


async def test_get_me_impersonated_shows_impersonated_by(
    client: AsyncClient, make_trainer: MakeTrainer, impersonation_token: Any
) -> None:
    imp = await impersonation_token(await make_trainer())
    r = await client.get(ME, headers=bearer(imp.access))
    assert r.json()["impersonated_by"] == str(imp.admin_id)
    assert r.headers["X-Impersonated-By"] == str(imp.admin_id)


async def test_patch_me_updates_profile(
    client: AsyncClient, make_user: MakeUser, principal_token: PrincipalToken
) -> None:
    user = await make_user()
    r = await client.patch(
        ME,
        json={"first_name": "Ann", "phone": "+123", "school": "High", "timezone": "Europe/Paris"},
        headers=bearer(principal_token(user)),
    )
    assert r.status_code == 200
    body = r.json()
    assert (body["first_name"], body["phone"], body["school"], body["timezone"]) == (
        "Ann",
        "+123",
        "High",
        "Europe/Paris",
    )
    assert body["last_name"] == "User"  # absent field stays unchanged

    cleared = await client.patch(ME, json={"phone": None}, headers=bearer(principal_token(user)))
    assert (cleared.json()["phone"], cleared.json()["school"]) == (None, "High")


async def test_patch_me_rejects_unknown_fields(
    client: AsyncClient, make_user: MakeUser, principal_token: PrincipalToken
) -> None:
    user = await make_user()
    for extra in ({"role": "super_admin"}, {"email": "x@y.com"}, {"status": "active"}):
        r = await client.patch(
            ME, json={"first_name": "A", **extra}, headers=bearer(principal_token(user))
        )
        assert (r.status_code, code(r)) == (422, "VALIDATION_ERROR")


async def test_patch_me_null_for_required_field_is_422(
    client: AsyncClient, make_user: MakeUser, principal_token: PrincipalToken
) -> None:
    user = await make_user()
    r = await client.patch(ME, json={"first_name": None}, headers=bearer(principal_token(user)))
    assert (r.status_code, code(r)) == (422, "VALIDATION_ERROR")


@pytest.mark.parametrize("role", [UserRole.player, UserRole.trainer, UserRole.super_admin])
async def test_patch_me_coach_fields_forbidden_for_other_roles(
    client: AsyncClient,
    make_user: MakeUser,
    make_trainer: MakeTrainer,
    principal_token: PrincipalToken,
    role: UserRole,
) -> None:
    user = await (make_trainer() if role is UserRole.trainer else make_user(role=role))
    r = await client.patch(
        ME, json={"bio": "x", "is_public": True}, headers=bearer(principal_token(user))
    )
    assert (r.status_code, code(r)) == (422, "FIELD_NOT_ALLOWED")
    fields = {d["field"] for d in r.json()["error"]["details"]}
    assert fields == {"bio", "is_public"}


async def test_patch_me_coach_updates_public_profile(
    client: AsyncClient,
    make_trainer: MakeTrainer,
    make_coach: MakeCoach,
    principal_token: PrincipalToken,
    db_session: AsyncSession,
) -> None:
    trainer = await make_trainer()
    tenure: CoachProfile = await make_coach(trainer)
    headers = bearer(principal_token(tenure.user, trainer_id=trainer.id))
    r = await client.patch(
        ME,
        json={
            "bio": "Ex pro",
            "credentials": "UEFA B",
            "certifications": [{"name": "UEFA B", "issuer": "UEFA", "year": 2020}],
            "is_public": True,
            "first_name": "Cora",
        },
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["first_name"] == "Cora"
    assert r.json()["coach"] == {
        "bio": "Ex pro",
        "credentials": "UEFA B",
        "certifications": [{"name": "UEFA B", "issuer": "UEFA", "year": 2020}],
        "is_public": True,
    }
    await db_session.refresh(tenure)
    assert tenure.is_public is True


async def test_patch_me_bad_timezone_422(
    client: AsyncClient, make_user: MakeUser, principal_token: PrincipalToken
) -> None:
    user = await make_user()
    r = await client.patch(
        ME, json={"timezone": "Mars/Base"}, headers=bearer(principal_token(user))
    )
    assert (r.status_code, code(r)) == (422, "VALIDATION_ERROR")


@pytest.mark.perf
async def test_get_me_p95_under_100ms_smoke(
    client: AsyncClient, make_user: MakeUser, principal_token: PrincipalToken
) -> None:
    headers = bearer(principal_token(await make_user()))
    await client.get(ME, headers=headers)  # warm up
    samples = []
    for _ in range(50):
        started = time.perf_counter()
        assert (await client.get(ME, headers=headers)).status_code == 200
        samples.append(time.perf_counter() - started)
    samples.sort()
    assert samples[int(len(samples) * 0.95) - 1] < 0.1
