"""Every rate-limited route returns 429 + Retry-After one request past its limit (spec header)."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import pytest
from httpx import AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession

from tests.api.auth.helpers import LOGIN, code, login
from tests.api.helpers import bearer
from tests.factories import MakeUser

REGISTER = "/api/v1/auth/register?share_code=nope"
RESET_REQUEST = "/api/v1/auth/password-reset/request"
RESEND = "/api/v1/auth/verify-email/resend"
PREVIEW = "/api/v1/public/share-links/nope"
REGISTER_BODY = {
    "email": "new@x.com",
    "password": "Tr1cky-Purple-Elephant-42!",
    "first_name": "Ada",
    "last_name": "Lovelace",
    "birth_date": "1990-04-12",
}

Send = Callable[[int], Awaitable[Response]]


@dataclass(frozen=True)
class Scenario:
    send: Send
    allowed: int  # requests that must pass the limiter
    ok_status: int  # their status
    window_seconds: int


async def _login_ip(client: AsyncClient, make_user: MakeUser, db: AsyncSession) -> Scenario:
    async def send(i: int) -> Response:  # distinct emails: only the per-IP counter fills
        return await client.post(LOGIN, json={"email": f"u{i}@x.com", "password": "wrong-pw-1234"})

    return Scenario(send, 10, 401, 60)


async def _login_email(client: AsyncClient, make_user: MakeUser, db: AsyncSession) -> Scenario:
    async def send(i: int) -> Response:  # one email, case varied: the per-email counter fills
        email = "U@x.com" if i % 2 else "u@x.com"
        return await client.post(LOGIN, json={"email": email, "password": "wrong-pw-1234"})

    return Scenario(send, 5, 401, 60)


async def _register(client: AsyncClient, make_user: MakeUser, db: AsyncSession) -> Scenario:
    async def send(i: int) -> Response:
        return await client.post(REGISTER, json=REGISTER_BODY)

    return Scenario(send, 20, 404, 3600)


async def _reset(client: AsyncClient, make_user: MakeUser, db: AsyncSession) -> Scenario:
    async def send(i: int) -> Response:
        return await client.post(RESET_REQUEST, json={"email": "nobody@x.com"})

    return Scenario(send, 5, 202, 3600)


async def _resend(client: AsyncClient, make_user: MakeUser, db: AsyncSession) -> Scenario:
    user = await make_user(email="u@x.com")
    user.is_email_verified = False
    await db.flush()
    headers = bearer((await login(client, "u@x.com"))["tokens"]["access_token"])

    async def send(i: int) -> Response:
        return await client.post(RESEND, headers=headers)

    return Scenario(send, 3, 202, 3600)


async def _preview(client: AsyncClient, make_user: MakeUser, db: AsyncSession) -> Scenario:
    async def send(i: int) -> Response:
        return await client.get(PREVIEW)

    return Scenario(send, 60, 404, 60)


SCENARIOS = {
    "login_10_per_min_per_ip": _login_ip,
    "login_5_per_min_per_email": _login_email,
    "register_20_per_hour": _register,
    "password_reset_5_per_hour": _reset,
    "verify_resend_3_per_hour": _resend,
    "share_link_preview_60_per_min": _preview,
}


@pytest.mark.parametrize("name", SCENARIOS)
async def test_route_returns_429_with_retry_after_after_limit(
    name: str, client: AsyncClient, make_user: MakeUser, db_session: AsyncSession
) -> None:
    sc = await SCENARIOS[name](client, make_user, db_session)
    for i in range(sc.allowed):
        assert (await sc.send(i)).status_code == sc.ok_status, f"request {i + 1}"
    limited = await sc.send(sc.allowed)
    assert limited.status_code == 429
    assert code(limited) == "RATE_LIMITED"
    assert 1 <= int(limited.headers["Retry-After"]) <= sc.window_seconds
