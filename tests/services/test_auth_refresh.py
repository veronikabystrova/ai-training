import asyncio
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.security import PasswordHasher
from app.models.identity import Profile, User, UserRole, UserStatus
from app.models.platform import AuditLog
from tests.api.auth.helpers import LOGIN, REFRESH, code
from tests.factories import TEST_PASSWORD


async def _committed_user(engine: AsyncEngine, email: str) -> User:
    digest = await PasswordHasher(time_cost=1, memory_kib=8, parallelism=1).hash(TEST_PASSWORD)
    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = User(
            email=email,
            password_hash=digest,
            role=UserRole.player,
            status=UserStatus.active,
            is_email_verified=True,
            profile=Profile(first_name="T", last_name="U"),
        )
        session.add(user)
        await session.commit()
    return user


@pytest.mark.gate
@pytest.mark.concurrency
async def test_refresh_parallel_same_token_one_success(
    committed_app: FastAPI, committed_client: AsyncClient, committed_engine: AsyncEngine
) -> None:
    await _committed_user(committed_engine, "u@x.com")
    login = await committed_client.post(LOGIN, json={"email": "u@x.com", "password": TEST_PASSWORD})
    token = login.json()["tokens"]["refresh_token"]

    results = await asyncio.gather(
        *[committed_client.post(REFRESH, json={"refresh_token": token}) for _ in range(5)]
    )
    statuses = sorted(r.status_code for r in results)
    assert statuses == [200] + [401] * 4
    losers: list[Any] = [r for r in results if r.status_code == 401]
    # Row locks serialise the losers: the first sees a consumed token and revokes the family,
    # the rest then see a revoked one.
    assert sorted(code(r) for r in losers) == ["INVALID_REFRESH_TOKEN"] * 3 + [
        "REFRESH_REUSE_DETECTED"
    ]

    # The winner's successor died with the family.
    winner = next(r for r in results if r.status_code == 200)
    follow = await committed_client.post(
        REFRESH, json={"refresh_token": winner.json()["refresh_token"]}
    )
    assert code(follow) == "INVALID_REFRESH_TOKEN"

    async with AsyncSession(committed_engine) as session:
        audits = (
            (
                await session.execute(
                    select(AuditLog).where(AuditLog.action == "auth.refresh_reuse_detected")
                )
            )
            .scalars()
            .all()
        )
    assert len(audits) == 1
