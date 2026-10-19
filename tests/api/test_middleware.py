from collections.abc import Awaitable, Callable
from typing import Any

import pytest
import structlog
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import PrincipalDep
from app.core.config import Settings
from app.main import create_app
from tests.api.helpers import bearer
from tests.factories import ImpersonationToken, MakeTrainer

ImpersonationFactory = Callable[..., Awaitable[ImpersonationToken]]


async def test_correlation_id_always_present_and_echoed(client: AsyncClient) -> None:
    r = await client.get(
        "/api/v1/public/trainers/none/branding", headers={"X-Correlation-Id": "abc"}
    )
    assert r.headers["X-Correlation-Id"] == "abc"
    health = await client.get("/healthz")
    assert health.json() == {"status": "ok"}
    assert health.headers["X-Correlation-Id"]


@pytest.mark.parametrize("supplied", ["", "has space", "x" * 65, "semi;colon"])
async def test_unusable_correlation_id_is_replaced(client: AsyncClient, supplied: str) -> None:
    r = await client.get("/healthz", headers={"X-Correlation-Id": supplied})
    cid = r.headers["X-Correlation-Id"]
    assert cid != supplied
    assert 1 <= len(cid) <= 64


async def test_correlation_id_on_error_responses(client: AsyncClient) -> None:
    r = await client.get("/probe/principal", headers={"X-Correlation-Id": "e1"})
    assert r.status_code == 401
    assert r.headers["X-Correlation-Id"] == "e1"


async def test_impersonated_request_sets_header_and_writes_audit(
    client: AsyncClient,
    make_trainer: MakeTrainer,
    impersonation_token: ImpersonationFactory,
    db_session: AsyncSession,
) -> None:
    trainer = await make_trainer()
    imp = await impersonation_token(trainer)
    r = await client.get("/probe/principal", headers=bearer(imp.access))
    assert r.headers["X-Impersonated-By"] == str(imp.admin_id)
    rows = (
        await db_session.execute(
            text(
                "select method, path, action, actor_user_id, act_user_id, trainer_id "
                "from audit_log where act_user_id is not null"
            )
        )
    ).all()
    assert rows == [
        ("GET", "/probe/principal", "impersonated_request", trainer.id, imp.admin_id, trainer.id)
    ]


async def test_impersonated_me_request_writes_audit(
    client: AsyncClient,
    make_trainer: MakeTrainer,
    impersonation_token: ImpersonationFactory,
    db_session: AsyncSession,
) -> None:
    imp = await impersonation_token(await make_trainer())
    r = await client.get("/api/v1/me", headers=bearer(imp.access))
    assert r.headers["X-Impersonated-By"] == str(imp.admin_id)
    rows = (
        await db_session.execute(
            text("select method, path from audit_log where act_user_id is not null")
        )
    ).all()
    assert ("GET", "/api/v1/me") in rows


async def test_rejected_impersonation_request_is_neither_marked_nor_audited(
    client: AsyncClient,
    make_trainer: MakeTrainer,
    impersonation_token: ImpersonationFactory,
    db_session: AsyncSession,
    clock: Any,
) -> None:
    imp = await impersonation_token(await make_trainer(), minutes=1)
    clock.advance(minutes=2)
    r = await client.get("/probe/principal", headers=bearer(imp.access))
    assert r.status_code == 401
    assert "X-Impersonated-By" not in r.headers
    count = (await db_session.execute(text("select count(*) from audit_log"))).scalar_one()
    assert count == 0


async def test_normal_request_has_no_impersonation_header_or_audit(
    client: AsyncClient, trainer_token: str, db_session: AsyncSession
) -> None:
    r = await client.get("/probe/principal", headers=bearer(trainer_token))
    assert r.status_code == 200
    assert "X-Impersonated-By" not in r.headers
    count = (await db_session.execute(text("select count(*) from audit_log"))).scalar_one()
    assert count == 0


async def test_unhandled_error_logged_once_with_correlation_id(settings: Settings) -> None:
    app = create_app(settings)

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("boom")

    # The default ASGITransport re-raises app exceptions: the test errors if one escapes.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        with structlog.testing.capture_logs() as logs:
            r = await c.get("/boom", headers={"X-Correlation-Id": "abc"})
    assert r.status_code == 500
    assert r.json()["error"]["code"] == "INTERNAL_ERROR"
    assert r.headers["X-Correlation-Id"] == "abc"
    assert [e["event"] for e in logs].count("unhandled_exception") == 1


async def test_domain_errors_are_not_logged_as_unhandled(client: AsyncClient) -> None:
    with structlog.testing.capture_logs() as logs:
        r = await client.get("/probe/principal")
    assert r.status_code == 401
    assert [e for e in logs if e["event"] == "unhandled_exception"] == []


async def test_impersonated_request_that_crashes_is_still_audited(
    app: FastAPI,
    make_trainer: MakeTrainer,
    impersonation_token: ImpersonationFactory,
    db_session: AsyncSession,
) -> None:
    @app.get("/probe/crash")
    async def crash(p: PrincipalDep) -> None:
        raise RuntimeError("crash")

    imp = await impersonation_token(await make_trainer())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/probe/crash", headers=bearer(imp.access))
    assert r.status_code == 500
    rows = (
        await db_session.execute(
            text("select method, path, action from audit_log where act_user_id is not null")
        )
    ).all()
    assert rows == [("GET", "/probe/crash", "impersonated_request")]
