"""Every TTL, one second either side, against tokens issued by the real endpoints."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession

from tests.api.auth.helpers import REFRESH, code, login
from tests.api.helpers import bearer
from tests.factories import (
    AddShareLink,
    LinkPlayer,
    MakeApproval,
    MakeDependent,
    MakeTrainer,
    MakeUser,
    PrincipalToken,
)
from tests.fakes import FakeClock, FakeJobQueue

STRONG = "Tr1cky-Purple-Elephant-42!"
ADMIN_USERS = "/api/v1/admin/users"


@dataclass
class Env:
    client: AsyncClient
    clock: FakeClock
    db: AsyncSession
    jobs: FakeJobQueue
    make_user: MakeUser
    make_trainer: MakeTrainer
    make_dependent: MakeDependent
    link_player: LinkPlayer
    make_approval: MakeApproval
    principal_token: PrincipalToken
    admin_token: str
    add_share_link: AddShareLink


@dataclass(frozen=True)
class Case:
    ttl: timedelta
    issue: Callable[[Env, int], Awaitable[Any]]  # i = 0 or 1: two independent artifacts
    consume: Callable[[Env, Any], Awaitable[Response]]
    ok: int
    expired: int
    expired_code: str | None = None


def _last_token(env: Env) -> str:
    return env.jobs.enqueued[-1].kwargs["token"]


async def _issue_access(env: Env, i: int) -> str:
    await env.make_user(email=f"a{i}@x.com")
    return str((await login(env.client, f"a{i}@x.com"))["tokens"]["access_token"])


async def _use_access(env: Env, token: str) -> Response:
    return await env.client.get("/api/v1/me", headers=bearer(token))


async def _issue_refresh(env: Env, i: int) -> str:
    await env.make_user(email=f"r{i}@x.com")
    return str((await login(env.client, f"r{i}@x.com"))["tokens"]["refresh_token"])


async def _use_refresh(env: Env, token: str) -> Response:
    return await env.client.post(REFRESH, json={"refresh_token": token})


async def _issue_impersonation(env: Env, i: int) -> str:
    target = await env.make_trainer()
    r = await env.client.post(
        f"{ADMIN_USERS}/{target.id}/impersonate", headers=bearer(env.admin_token)
    )
    assert r.status_code == 200, r.text
    return str(r.json()["access_token"])


async def _use_probe(env: Env, token: str) -> Response:
    return await env.client.get("/probe/principal", headers=bearer(token))


async def _issue_reset(env: Env, i: int) -> str:
    await env.make_user(email=f"p{i}@x.com")
    r = await env.client.post("/api/v1/auth/password-reset/request", json={"email": f"p{i}@x.com"})
    assert r.status_code == 202
    return _last_token(env)


async def _use_reset(env: Env, token: str) -> Response:
    return await env.client.post(
        "/api/v1/auth/password-reset/confirm", json={"token": token, "new_password": STRONG}
    )


async def _issue_verify(env: Env, i: int) -> str:
    user = await env.make_user(email=f"v{i}@x.com")
    user.is_email_verified = False
    await env.db.flush()
    headers = bearer(env.principal_token(user))
    assert (
        await env.client.post("/api/v1/auth/verify-email/resend", headers=headers)
    ).status_code == 202
    return _last_token(env)


async def _use_verify(env: Env, token: str) -> Response:
    return await env.client.post("/api/v1/auth/verify-email", json={"token": token})


async def _issue_setup(env: Env, i: int) -> str:
    r = await env.client.post(
        f"{ADMIN_USERS}/trainers",
        headers=bearer(env.admin_token),
        json={
            "email": f"t{i}@example.com",
            "first_name": "Nora",
            "last_name": "Net",
            "business_name": f"Net {i}",
            "slug": f"net-{i}",
            "timezone": "Europe/Paris",
        },
    )
    assert r.status_code == 201, r.text
    return _last_token(env)


async def _use_setup(env: Env, token: str) -> Response:
    return await env.client.post(
        "/api/v1/auth/password/setup", json={"token": token, "new_password": STRONG}
    )


async def _issue_coach_link(env: Env, i: int) -> tuple[str, str]:
    trainer = await env.make_trainer()
    r = await env.client.post(
        "/api/v1/trainer/share-links",
        json={"kind": "coach_unique", "target_email": f"c{i}@x.com"},
        headers=bearer(env.principal_token(trainer)),
    )
    assert r.status_code == 201, r.text
    return f"c{i}@x.com", str(r.json()["code"])


async def _use_coach_link(env: Env, issued: tuple[str, str]) -> Response:
    email, share_code = issued
    return await env.client.post(
        f"/api/v1/auth/register?share_code={share_code}",
        json={
            "email": email,
            "password": STRONG,
            "first_name": "Sam",
            "last_name": "Coach",
            "birth_date": "1990-04-12",
        },
    )


async def _issue_approval(env: Env, i: int) -> tuple[Any, Any]:
    parent = await env.make_user()
    trainer = await env.make_trainer()
    child = await env.make_dependent(parent=parent)
    await env.link_player(trainer, child)
    return parent, await env.make_approval(parent=parent, trainer=trainer, player=child)


async def _use_approval(env: Env, issued: tuple[Any, Any]) -> Response:
    parent, approval = issued
    headers = {**bearer(env.principal_token(parent)), "Idempotency-Key": f"k-{approval.id}"}
    return await env.client.post(f"/api/v1/me/approvals/{approval.id}/approve", headers=headers)


CASES = {
    "access": Case(timedelta(seconds=900), _issue_access, _use_access, 200, 401),
    "refresh": Case(timedelta(days=30), _issue_refresh, _use_refresh, 200, 401),
    "impersonation": Case(timedelta(hours=1), _issue_impersonation, _use_probe, 200, 401),
    "password_reset": Case(timedelta(hours=1), _issue_reset, _use_reset, 204, 400, "TOKEN_INVALID"),
    "email_verify": Case(
        timedelta(hours=24), _issue_verify, _use_verify, 204, 400, "TOKEN_INVALID"
    ),
    "trainer_setup": Case(timedelta(hours=24), _issue_setup, _use_setup, 200, 400, "TOKEN_INVALID"),
    "coach_link": Case(
        timedelta(days=7), _issue_coach_link, _use_coach_link, 201, 404, "SHARE_LINK_INVALID"
    ),
    "approval": Case(
        timedelta(hours=48), _issue_approval, _use_approval, 200, 409, "APPROVAL_NOT_PENDING"
    ),
}


@pytest.fixture
def env(
    client: AsyncClient,
    clock: FakeClock,
    db_session: AsyncSession,
    jobs: FakeJobQueue,
    make_user: MakeUser,
    make_trainer: MakeTrainer,
    make_dependent: MakeDependent,
    link_player: LinkPlayer,
    make_approval: MakeApproval,
    principal_token: PrincipalToken,
    admin_token: str,
    add_share_link: AddShareLink,
) -> Env:
    return Env(
        client, clock, db_session, jobs, make_user, make_trainer, make_dependent, link_player,
        make_approval, principal_token, admin_token, add_share_link,
    )  # fmt: skip


@pytest.mark.parametrize("what", CASES)
async def test_ttl_boundaries_one_second_either_side(what: str, env: Env) -> None:
    case = CASES[what]
    inside = await case.issue(env, 0)
    outside = await case.issue(env, 1)

    env.clock.advance(seconds=case.ttl.total_seconds() - 1)
    r = await case.consume(env, inside)
    assert r.status_code == case.ok, f"{what} one second before expiry: {r.text}"

    env.clock.advance(seconds=2)  # one second past the TTL
    r = await case.consume(env, outside)
    assert r.status_code == case.expired, f"{what} one second after expiry: {r.text}"
    if case.expired_code:
        assert code(r) == case.expired_code
