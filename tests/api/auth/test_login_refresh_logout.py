from datetime import timedelta

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import new_id
from app.core.security import PasswordHasher, TokenIssuer, hash_token
from app.models.identity import User, UserRole, UserStatus
from app.models.platform import AuditLog
from app.repositories.coach_profile import CoachProfileRepository
from app.repositories.impersonation import ImpersonationSessionRepository
from app.repositories.refresh_token import RefreshTokenRepository
from tests.api.auth.helpers import LOGIN, LOGOUT, REFRESH, code, login
from tests.api.helpers import bearer
from tests.factories import (
    TEST_JWT_SECRET,
    TEST_PASSWORD,
    MakeCoach,
    MakeDependent,
    MakeTrainer,
    MakeUser,
)
from tests.fakes import FakeClock

PW = TEST_PASSWORD


def decode(token: str, clock: FakeClock) -> dict[str, object]:
    return TokenIssuer(secret=TEST_JWT_SECRET, access_ttl=900, clock=clock).decode(token)


# --- login ---------------------------------------------------------------------------------


async def test_login_returns_pair_and_session_user(
    client: AsyncClient, make_trainer: MakeTrainer
) -> None:
    t = await make_trainer(email="t@x.com")
    r = await client.post(LOGIN, json={"email": "t@x.com", "password": PW})
    body = r.json()
    assert r.status_code == 200
    assert body["user"]["role"] == "trainer"
    assert body["user"]["trainer_id"] == str(t.id)
    assert body["user"]["email"] == "t@x.com"
    assert body["user"]["email_verified"] is True
    assert body["user"]["must_change_password"] is False
    assert body["tokens"]["expires_in"] == 900
    assert body["tokens"]["token_type"] == "bearer"
    assert "password_hash" not in r.text


async def test_login_email_is_case_insensitive(client: AsyncClient, make_user: MakeUser) -> None:
    await make_user(email="mixed@x.com")
    r = await client.post(LOGIN, json={"email": "MIXED@x.com", "password": PW})
    assert r.status_code == 200


async def test_login_access_token_authenticates(
    client: AsyncClient, make_trainer: MakeTrainer
) -> None:
    await make_trainer(email="t@x.com")
    body = await login(client, "t@x.com")
    r = await client.get("/probe/principal", headers=bearer(body["tokens"]["access_token"]))
    assert r.status_code == 200
    assert r.json()["role"] == "trainer"


@pytest.mark.parametrize("state", ["unknown", "wrong_password", "pending", "inactive", "deleted"])
async def test_login_failure_is_one_identical_401(
    client: AsyncClient, state: str, make_user: MakeUser
) -> None:
    password = PW
    if state == "wrong_password":
        await make_user(email="u@x.com")
        password = "Not-The-Right-Password-1"
    elif state in {"pending", "inactive", "deleted"}:
        await make_user(email="u@x.com", status=UserStatus(state))
    r = await client.post(LOGIN, json={"email": "u@x.com", "password": password})
    assert r.status_code == 401
    assert code(r) == "INVALID_CREDENTIALS"
    assert r.json() == {
        "error": {"code": "INVALID_CREDENTIALS", "message": "Invalid credentials", "details": []}
    }
    assert r.headers["WWW-Authenticate"] == "Bearer"


async def test_login_rate_limit_5_per_minute_per_email(
    client: AsyncClient, make_user: MakeUser
) -> None:
    await make_user(email="u@x.com")
    for _ in range(5):
        r = await client.post(LOGIN, json={"email": "u@x.com", "password": "wrong-password-1"})
        assert r.status_code == 401
    r = await client.post(LOGIN, json={"email": "U@x.com", "password": PW})
    assert r.status_code == 429
    assert code(r) == "RATE_LIMITED"
    assert int(r.headers["Retry-After"]) >= 1
    # A different email is not affected.
    other = await client.post(LOGIN, json={"email": "other@x.com", "password": PW})
    assert other.status_code == 401


async def test_login_rate_limit_10_per_minute_per_ip(client: AsyncClient) -> None:
    for i in range(10):
        r = await client.post(LOGIN, json={"email": f"u{i}@x.com", "password": "wrong-password-1"})
        assert r.status_code == 401
    r = await client.post(LOGIN, json={"email": "u99@x.com", "password": "wrong-password-1"})
    assert r.status_code == 429


async def test_login_rejects_unknown_fields_and_bad_email(client: AsyncClient) -> None:
    r = await client.post(LOGIN, json={"email": "u@x.com", "password": PW, "extra": 1})
    assert r.status_code == 422
    r = await client.post(LOGIN, json={"email": "not-an-email", "password": PW})
    assert r.status_code == 422


async def test_login_sets_last_login_and_child_flag(
    client: AsyncClient,
    db_session: AsyncSession,
    make_dependent: MakeDependent,
    make_user: MakeUser,
    clock: FakeClock,
) -> None:
    child_profile = await make_dependent(with_login=True)
    assert child_profile.login_user_id is not None
    child = await db_session.get(User, child_profile.login_user_id)
    assert child is not None
    parent = await make_user(email="parent@x.com")

    assert (await login(client, child.email))["user"]["child"] is True
    assert (await login(client, "parent@x.com"))["user"]["child"] is False
    await db_session.refresh(child)
    await db_session.refresh(parent)
    assert child.last_login_at == clock.now()
    assert parent.last_login_at == clock.now()


async def test_login_coach_gets_tenant_and_player_none(
    client: AsyncClient, make_trainer: MakeTrainer, make_coach: MakeCoach, make_user: MakeUser
) -> None:
    trainer = await make_trainer()
    coach = await make_coach(trainer, user=await make_user(role=UserRole.coach, email="c@x.com"))
    await make_user(email="p@x.com")
    coach_body = await login(client, "c@x.com")
    assert coach_body["user"]["trainer_id"] == str(coach.trainer_id)
    assert (await login(client, "p@x.com"))["user"]["trainer_id"] is None


async def test_login_upgrades_outdated_password_hash(
    client: AsyncClient, app: FastAPI, db_session: AsyncSession, make_user: MakeUser
) -> None:
    user = await make_user(email="u@x.com")
    old = user.password_hash
    app.state.hasher = PasswordHasher(time_cost=2, memory_kib=8, parallelism=1)
    await login(client, "u@x.com")
    await db_session.refresh(user)
    assert user.password_hash != old
    assert await app.state.hasher.verify(user.password_hash, PW)


async def test_login_stores_hashed_client_fingerprint_only(
    client: AsyncClient, db_session: AsyncSession, make_user: MakeUser
) -> None:
    user = await make_user(email="u@x.com")
    body = await login(client, "u@x.com")
    row = await RefreshTokenRepository(db_session).lock_by_hash(
        hash_token(body["tokens"]["refresh_token"])
    )
    assert row is not None
    assert row.user_id == user.id
    assert row.ip_hash is not None and len(row.ip_hash) == 64
    assert row.user_agent_hash is None or "python" not in row.user_agent_hash.lower()


# --- refresh -------------------------------------------------------------------------------


async def test_refresh_rotates_and_rebuilds_claims(
    client: AsyncClient,
    db_session: AsyncSession,
    make_trainer: MakeTrainer,
    make_coach: MakeCoach,
    make_user: MakeUser,
    clock: FakeClock,
) -> None:
    t1, t2 = await make_trainer(), await make_trainer()
    coach_user = await make_user(role=UserRole.coach, email="c@x.com")
    await make_coach(t1, user=coach_user)
    first = (await login(client, "c@x.com"))["tokens"]
    assert decode(first["access_token"], clock)["tid"] == str(t1.id)

    # The coach moves tenant after the token was issued: claims must follow the DB.
    await CoachProfileRepository(db_session).close_tenure(
        trainer_id=t1.id, user_id=coach_user.id, now=clock.now()
    )
    await make_coach(t2, user=coach_user)

    r = await client.post(REFRESH, json={"refresh_token": first["refresh_token"]})
    assert r.status_code == 200
    second = r.json()
    assert second["refresh_token"] != first["refresh_token"]
    assert second["token_type"] == "bearer"
    assert second["expires_in"] == 900
    assert decode(second["access_token"], clock)["tid"] == str(t2.id)


async def test_refresh_reuse_revokes_family(
    client: AsyncClient, db_session: AsyncSession, make_user: MakeUser
) -> None:
    user = await make_user(email="u@x.com")
    a = (await login(client, "u@x.com"))["tokens"]["refresh_token"]
    b = (await client.post(REFRESH, json={"refresh_token": a})).json()["refresh_token"]

    replay = await client.post(REFRESH, json={"refresh_token": a})
    assert replay.status_code == 401
    assert code(replay) == "REFRESH_REUSE_DETECTED"

    successor = await client.post(REFRESH, json={"refresh_token": b})
    assert successor.status_code == 401
    assert code(successor) == "INVALID_REFRESH_TOKEN"

    audit = (
        (
            await db_session.execute(
                select(AuditLog).where(AuditLog.action == "auth.refresh_reuse_detected")
            )
        )
        .scalars()
        .all()
    )
    assert len(audit) == 1
    assert audit[0].actor_user_id == user.id


async def test_refresh_reuse_of_an_old_token_does_not_touch_other_families(
    client: AsyncClient, make_user: MakeUser
) -> None:
    await make_user(email="u@x.com")
    laptop = (await login(client, "u@x.com"))["tokens"]["refresh_token"]
    phone = (await login(client, "u@x.com"))["tokens"]["refresh_token"]
    await client.post(REFRESH, json={"refresh_token": laptop})
    await client.post(REFRESH, json={"refresh_token": laptop})  # reuse: laptop family dies
    assert (await client.post(REFRESH, json={"refresh_token": phone})).status_code == 200


async def test_refresh_expiry_boundary_30_days(
    client: AsyncClient, make_user: MakeUser, clock: FakeClock
) -> None:
    await make_user(email="u@x.com")
    just_inside = (await login(client, "u@x.com"))["tokens"]["refresh_token"]
    just_outside = (await login(client, "u@x.com"))["tokens"]["refresh_token"]

    clock.advance(days=30, seconds=-1)
    assert (await client.post(REFRESH, json={"refresh_token": just_inside})).status_code == 200
    clock.advance(seconds=2)
    r = await client.post(REFRESH, json={"refresh_token": just_outside})
    assert r.status_code == 401
    assert code(r) == "INVALID_REFRESH_TOKEN"


async def test_refresh_unknown_token_is_invalid_refresh_token(client: AsyncClient) -> None:
    r = await client.post(REFRESH, json={"refresh_token": "x" * 43})
    assert r.status_code == 401
    assert code(r) == "INVALID_REFRESH_TOKEN"


async def test_refresh_validates_token_length(client: AsyncClient) -> None:
    assert (await client.post(REFRESH, json={"refresh_token": "short"})).status_code == 422
    assert (await client.post(REFRESH, json={"refresh_token": "x" * 201})).status_code == 422


@pytest.mark.parametrize("status", [UserStatus.inactive, UserStatus.deleted])
async def test_refresh_for_deactivated_user_is_invalid(
    client: AsyncClient, db_session: AsyncSession, make_user: MakeUser, status: UserStatus
) -> None:
    user = await make_user(email="u@x.com")
    token = (await login(client, "u@x.com"))["tokens"]["refresh_token"]
    user.status = status
    await db_session.flush()
    r = await client.post(REFRESH, json={"refresh_token": token})
    assert r.status_code == 401
    assert code(r) == "INVALID_REFRESH_TOKEN"


async def _impersonation_family(
    db_session: AsyncSession,
    make_trainer: MakeTrainer,
    make_user: MakeUser,
    clock: FakeClock,
) -> tuple[User, User, object, str]:
    admin = await make_user(role=UserRole.super_admin)
    target = await make_trainer()
    jti = new_id()
    await ImpersonationSessionRepository(db_session).open(
        admin_user_id=admin.id,
        target_user_id=target.id,
        jti=jti,
        expires_at=clock.now() + timedelta(minutes=60),
        reason=None,
    )
    raw = await RefreshTokenRepository(db_session).issue(
        user_id=target.id, family_id=jti, expires_at=clock.now() + timedelta(days=30)
    )
    return admin, target, jti, raw


async def test_impersonation_refresh_keeps_act_and_is_capped_at_session_expiry(
    client: AsyncClient,
    db_session: AsyncSession,
    make_trainer: MakeTrainer,
    make_user: MakeUser,
    clock: FakeClock,
) -> None:
    admin, target, jti, raw = await _impersonation_family(
        db_session, make_trainer, make_user, clock
    )
    clock.advance(minutes=30)
    r = await client.post(REFRESH, json={"refresh_token": raw})
    assert r.status_code == 200
    claims = decode(r.json()["access_token"], clock)
    assert claims["sub"] == str(target.id)
    assert claims["act"] == str(admin.id)
    assert claims["jti"] == str(jti)
    # The new access token is accepted by the DB revalidation (same jti as the session).
    ok = await client.get("/probe/principal", headers=bearer(r.json()["access_token"]))
    assert ok.status_code == 200
    assert ok.json()["act"] == str(admin.id)

    successor = await RefreshTokenRepository(db_session).lock_by_hash(
        hash_token(r.json()["refresh_token"])
    )
    assert successor is not None
    assert successor.expires_at == clock.now() + timedelta(minutes=30)  # session end, not 30 d


async def test_impersonation_refresh_after_session_end_is_expired(
    client: AsyncClient,
    db_session: AsyncSession,
    make_trainer: MakeTrainer,
    make_user: MakeUser,
    clock: FakeClock,
) -> None:
    _, _, _, raw = await _impersonation_family(db_session, make_trainer, make_user, clock)
    clock.advance(minutes=60, seconds=1)
    r = await client.post(REFRESH, json={"refresh_token": raw})
    assert r.status_code == 401
    assert code(r) == "IMPERSONATION_EXPIRED"
    again = await client.post(REFRESH, json={"refresh_token": raw})
    assert code(again) == "INVALID_REFRESH_TOKEN"  # the family was revoked


async def test_impersonation_refresh_after_admin_deactivated_is_expired(
    client: AsyncClient,
    db_session: AsyncSession,
    make_trainer: MakeTrainer,
    make_user: MakeUser,
    clock: FakeClock,
) -> None:
    admin, _, _, raw = await _impersonation_family(db_session, make_trainer, make_user, clock)
    admin.status = UserStatus.inactive
    await db_session.flush()
    r = await client.post(REFRESH, json={"refresh_token": raw})
    assert code(r) == "IMPERSONATION_EXPIRED"


# --- logout --------------------------------------------------------------------------------


async def test_logout_revokes_family_204_and_foreign_token_404(
    client: AsyncClient, make_user: MakeUser
) -> None:
    await make_user(email="a@x.com")
    await make_user(email="b@x.com")
    a = await login(client, "a@x.com")
    b = await login(client, "b@x.com")
    a_headers = bearer(a["tokens"]["access_token"])

    foreign = await client.post(
        LOGOUT, json={"refresh_token": b["tokens"]["refresh_token"]}, headers=a_headers
    )
    assert foreign.status_code == 404
    assert code(foreign) == "NOT_FOUND"
    unknown = await client.post(LOGOUT, json={"refresh_token": "x" * 43}, headers=a_headers)
    assert unknown.status_code == 404
    # B's family is untouched by A's failed attempt.
    assert (
        await client.post(REFRESH, json={"refresh_token": b["tokens"]["refresh_token"]})
    ).status_code == 200

    r = await client.post(
        LOGOUT, json={"refresh_token": a["tokens"]["refresh_token"]}, headers=a_headers
    )
    assert r.status_code == 204
    assert r.content == b""
    after = await client.post(REFRESH, json={"refresh_token": a["tokens"]["refresh_token"]})
    assert after.status_code == 401
    assert code(after) == "INVALID_REFRESH_TOKEN"


async def test_logout_revokes_the_rotated_successor_too(
    client: AsyncClient, make_user: MakeUser
) -> None:
    await make_user(email="a@x.com")
    first = (await login(client, "a@x.com"))["tokens"]
    second = (await client.post(REFRESH, json={"refresh_token": first["refresh_token"]})).json()
    r = await client.post(
        LOGOUT,
        json={"refresh_token": second["refresh_token"]},
        headers=bearer(second["access_token"]),
    )
    assert r.status_code == 204
    assert (
        await client.post(REFRESH, json={"refresh_token": second["refresh_token"]})
    ).status_code == 401


@pytest.mark.parametrize("token", ["short", "x" * 201])
async def test_logout_refresh_token_length_is_bounded(
    client: AsyncClient, make_user: MakeUser, token: str
) -> None:
    await make_user(email="a@x.com")
    access = (await login(client, "a@x.com"))["tokens"]["access_token"]
    r = await client.post(LOGOUT, json={"refresh_token": token}, headers=bearer(access))
    assert r.status_code == 422


async def test_logout_requires_authentication(client: AsyncClient) -> None:
    r = await client.post(LOGOUT, json={"refresh_token": "x" * 43})
    assert r.status_code == 401


async def test_logout_allowed_while_must_change_password(
    client: AsyncClient, db_session: AsyncSession, make_user: MakeUser
) -> None:
    user = await make_user(email="a@x.com")
    user.must_change_password = True
    await db_session.flush()
    body = await login(client, "a@x.com")
    assert body["user"]["must_change_password"] is True
    r = await client.post(
        LOGOUT,
        json={"refresh_token": body["tokens"]["refresh_token"]},
        headers=bearer(body["tokens"]["access_token"]),
    )
    assert r.status_code == 204


async def test_logout_twice_is_idempotent(client: AsyncClient, make_user: MakeUser) -> None:
    await make_user(email="a@x.com")
    body = await login(client, "a@x.com")
    payload = {"refresh_token": body["tokens"]["refresh_token"]}
    headers = bearer(body["tokens"]["access_token"])
    assert (await client.post(LOGOUT, json=payload, headers=headers)).status_code == 204
    assert (await client.post(LOGOUT, json=payload, headers=headers)).status_code == 204


# --- default rate limit (300/min: per IP on refresh, per principal on logout) ---------------


async def test_refresh_default_rate_limit_300_per_minute_per_ip(client: AsyncClient) -> None:
    body = {"refresh_token": "x" * 43}
    for _ in range(300):
        assert (await client.post(REFRESH, json=body)).status_code == 401
    r = await client.post(REFRESH, json=body)
    assert r.status_code == 429
    assert code(r) == "RATE_LIMITED"
    assert int(r.headers["Retry-After"]) >= 1


async def test_logout_default_rate_limit_300_per_minute_per_principal(
    client: AsyncClient, make_user: MakeUser
) -> None:
    await make_user(email="a@x.com")
    await make_user(email="b@x.com")
    a = bearer((await login(client, "a@x.com"))["tokens"]["access_token"])
    b = bearer((await login(client, "b@x.com"))["tokens"]["access_token"])
    body = {"refresh_token": "x" * 43}
    for _ in range(300):
        assert (await client.post(LOGOUT, json=body, headers=a)).status_code == 404
    assert (await client.post(LOGOUT, json=body, headers=a)).status_code == 429
    # Another principal on the same IP keeps its own budget.
    assert (await client.post(LOGOUT, json=body, headers=b)).status_code == 404
