from datetime import timedelta
from typing import Any

import jwt
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.identity import ImpersonationSession, RefreshToken, User, UserRole, UserStatus
from app.models.platform import AuditLog
from tests.api.auth.helpers import REFRESH
from tests.api.helpers import bearer
from tests.factories import TEST_JWT_SECRET, MakeCoach, MakeUser
from tests.fakes import FakeClock

USERS = "/api/v1/admin/users"


def code(response: Any) -> str:
    return str(response.json()["error"]["code"])


def claims(token: str) -> dict[str, Any]:
    decoded: dict[str, Any] = jwt.decode(
        token, TEST_JWT_SECRET, algorithms=["HS256"], options={"verify_exp": False}
    )
    return decoded


async def start(client: AsyncClient, admin_token: str, target: User) -> Any:
    return await client.post(f"{USERS}/{target.id}/impersonate", headers=bearer(admin_token))


async def test_impersonate_returns_60min_token_with_act_and_session(
    client: AsyncClient,
    admin_token: str,
    admin: User,
    make_trainer: MakeUser,
    db_session: AsyncSession,
    clock: FakeClock,
) -> None:
    target = await make_trainer()
    r = await start(client, admin_token, target)
    assert r.status_code == 200, r.text
    body = r.json()
    expires = clock.now() + timedelta(minutes=60)
    assert body["expires_at"].startswith(expires.strftime("%Y-%m-%dT%H:%M:%S"))
    assert body["target"] == {
        "id": str(target.id),
        "role": "trainer",
        "email": target.email,
        "email_verified": True,
        "must_change_password": False,
        "trainer_id": str(target.id),
        "child": False,
    }

    session = (await db_session.execute(select(ImpersonationSession))).scalar_one()
    assert (session.admin_user_id, session.target_user_id) == (admin.id, target.id)
    assert session.expires_at == expires and session.ended_at is None

    c = claims(body["access_token"])
    assert (c["sub"], c["act"], c["role"], c["tid"]) == (
        str(target.id),
        str(admin.id),
        "trainer",
        str(target.id),
    )
    assert c["exp"] - c["iat"] == 3600
    assert c["jti"] == str(session.jti)

    # the refresh family is keyed by the session's jti, which the refresh path relies on
    family = (await db_session.execute(select(RefreshToken))).scalars().all()
    assert len(family) == 1
    assert (family[0].family_id, family[0].user_id) == (session.jti, target.id)
    assert family[0].expires_at == session.expires_at

    me = await client.get("/probe/principal", headers=bearer(body["access_token"]))
    assert me.status_code == 200
    assert me.json()["act"] == str(admin.id) and me.json()["user_id"] == str(target.id)
    assert me.headers["X-Impersonated-By"] == str(admin.id)

    clock.advance(minutes=5)
    refreshed = await client.post(REFRESH, json={"refresh_token": body["refresh_token"]})
    assert refreshed.status_code == 200, refreshed.text
    rc = claims(refreshed.json()["access_token"])
    assert (rc["act"], rc["sub"], rc["jti"]) == (str(admin.id), str(target.id), str(session.jti))

    audit = (
        await db_session.execute(select(AuditLog).where(AuditLog.action == "impersonation.started"))
    ).scalar_one()
    assert audit.actor_user_id == admin.id and audit.subject_id == target.id


async def test_impersonate_coach_and_child_claims(
    client: AsyncClient,
    admin_token: str,
    make_trainer: MakeUser,
    make_coach: MakeCoach,
    make_user: MakeUser,
) -> None:
    trainer = await make_trainer()
    coach = await make_coach(trainer)
    r = await start(client, admin_token, coach.user)
    assert r.status_code == 200
    assert claims(r.json()["access_token"])["tid"] == str(trainer.id)
    assert r.json()["target"]["trainer_id"] == str(trainer.id)
    player = await make_user(role=UserRole.player)
    r = await start(client, admin_token, player)
    assert r.json()["target"]["trainer_id"] is None
    assert claims(r.json()["access_token"])["tid"] is None


async def test_impersonate_super_admin_403_inactive_target_409(
    client: AsyncClient, admin_token: str, make_user: MakeUser, db_session: AsyncSession
) -> None:
    other_admin = await make_user(role=UserRole.super_admin)
    r = await start(client, admin_token, other_admin)
    assert (r.status_code, code(r)) == (403, "CANNOT_IMPERSONATE_ADMIN")

    for status in (UserStatus.inactive, UserStatus.pending, UserStatus.deleted):
        target = await make_user(status=status)
        r = await start(client, admin_token, target)
        assert (r.status_code, code(r)) == (409, "USER_NOT_ACTIVE"), status

    unknown = await client.post(
        f"{USERS}/00000000-0000-7000-8000-000000000000/impersonate", headers=bearer(admin_token)
    )
    assert (unknown.status_code, code(unknown)) == (404, "NOT_FOUND")
    assert (await db_session.execute(select(ImpersonationSession))).first() is None
    assert (await db_session.execute(select(RefreshToken))).first() is None


async def test_impersonating_admin_cannot_reach_admin_routes_403(
    client: AsyncClient, admin_token: str, make_trainer: MakeUser
) -> None:
    target = await make_trainer()
    body = (await start(client, admin_token, target)).json()
    headers = bearer(body["access_token"])
    for method, path in (("GET", USERS), ("POST", f"{USERS}/{target.id}/impersonate")):
        r = await client.request(method, path, headers=headers)
        assert (r.status_code, code(r)) == (403, "FORBIDDEN"), path


async def test_impersonation_expiry_boundary_60_min(
    client: AsyncClient, admin_token: str, make_trainer: MakeUser, clock: FakeClock
) -> None:
    body = (await start(client, admin_token, await make_trainer())).json()
    headers = bearer(body["access_token"])
    clock.advance(minutes=59, seconds=59)
    assert (await client.get("/probe/principal", headers=headers)).status_code == 200
    clock.advance(seconds=1)  # exactly 60 minutes: expires_at <= now
    r = await client.get("/probe/principal", headers=headers)
    assert r.status_code == 401
    gone = await client.post(REFRESH, json={"refresh_token": body["refresh_token"]})
    assert gone.status_code == 401


async def test_exit_after_start_ends_the_session_and_its_family(
    client: AsyncClient, admin_token: str, make_trainer: MakeUser, db_session: AsyncSession
) -> None:
    body = (await start(client, admin_token, await make_trainer())).json()
    exit_ = await client.post(
        "/api/v1/auth/impersonation/exit", headers=bearer(body["access_token"])
    )
    assert exit_.status_code == 204
    gone = await client.post(REFRESH, json={"refresh_token": body["refresh_token"]})
    assert (gone.status_code, code(gone)) == (401, "INVALID_REFRESH_TOKEN")
