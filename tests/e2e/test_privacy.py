"""Privacy sweeps: no secret field in any API schema, no raw credential in any log line."""

import json
from typing import Any

import structlog.testing
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.email import LoggingEmailSender
from app.core.jobs import LoggingJobQueue
from tests.api.auth.helpers import LOGIN, REFRESH, login
from tests.factories import AddShareLink, MakeTrainer, MakeUser
from tests.fakes import FakeJobQueue

BANNED = {"password_hash", "token_hash", "ip_hash", "user_agent_hash", "legal_retention_until"}
STRONG = "Tr1cky-Purple-Elephant-42!"
NEW_STRONG = "Another-Sturdy-Walrus-77?"


def property_names(node: Any) -> set[str]:
    """Every ``properties`` key anywhere in the OpenAPI document (requests and responses)."""
    found: set[str] = set()
    if isinstance(node, dict):
        props = node.get("properties")
        if isinstance(props, dict):
            found |= set(props)
        for value in node.values():
            found |= property_names(value)
    elif isinstance(node, list):
        for value in node:
            found |= property_names(value)
    return found


async def test_no_response_schema_exposes_secret_fields(app: FastAPI) -> None:
    names = property_names(app.openapi())
    assert names, "OpenAPI document has no properties: the sweep would pass vacuously"
    assert not BANNED & names


async def test_logs_never_contain_raw_tokens_or_passwords(
    client: AsyncClient,
    make_user: MakeUser,
    make_trainer: MakeTrainer,
    add_share_link: AddShareLink,
    jobs: FakeJobQueue,
    db_session: AsyncSession,
    caplog: Any,
) -> None:
    secrets: set[str] = {STRONG, NEW_STRONG, "wrong-password-1"}
    with structlog.testing.capture_logs() as events:
        await make_user(email="u@x.com")
        await client.post(LOGIN, json={"email": "u@x.com", "password": "wrong-password-1"})
        tokens = (await login(client, "u@x.com"))["tokens"]
        refreshed = (
            await client.post(REFRESH, json={"refresh_token": tokens["refresh_token"]})
        ).json()
        secrets |= {tokens["access_token"], tokens["refresh_token"]}
        secrets |= {refreshed["access_token"], refreshed["refresh_token"]}
        await client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})

        link = await add_share_link(await make_trainer())
        r = await client.post(
            f"/api/v1/auth/register?share_code={link.code}",
            json={
                "email": "new@x.com",
                "password": STRONG,
                "first_name": "Ada",
                "last_name": "Lovelace",
                "birth_date": "1990-04-12",
            },
        )
        assert r.status_code == 201, r.text
        await client.post("/api/v1/auth/password-reset/request", json={"email": "u@x.com"})
        reset = jobs.enqueued[-1].kwargs["token"]
        secrets.add(reset)
        await client.post(
            "/api/v1/auth/password-reset/confirm", json={"token": reset, "new_password": NEW_STRONG}
        )
        # The development adapters receive raw tokens; they must log names only.
        await LoggingEmailSender().send(
            to="u@x.com", template="password_reset", context={"link": f"/r?token={reset}"}
        )
        await LoggingJobQueue().enqueue("send_password_reset", token=reset)

    for t in (j.kwargs.get("token") for j in jobs.enqueued):
        if t:
            secrets.add(t)
    assert events, "no log events captured: the sweep would pass vacuously"
    haystack = json.dumps(events, default=str) + caplog.text
    leaked = [s for s in secrets if s in haystack]
    assert not leaked, f"{len(leaked)} raw secret(s) leaked into logs"
    assert "u@x.com" not in haystack, "e-mail address leaked into logs"
