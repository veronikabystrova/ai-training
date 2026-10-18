import asyncio
from datetime import timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.ids import new_id
from app.models.identity import (
    CoachProfile,
    ShareLink,
    ShareLinkKind,
    TrainerPlayerLink,
    TrainerProfile,
    User,
    UserRole,
    UserStatus,
)
from tests.fakes import FakeClock

REGISTER = "/api/v1/auth/register"
STRONG = "Tr1cky-Purple-Elephant-42!"


class _NoLimit:
    """Registration is 20/h per IP; these tests deliberately come from one address."""

    async def hit(self, route: object, key: str) -> None:
        return None


async def _seed_link(
    engine: AsyncEngine, clock: FakeClock, *, kind: ShareLinkKind, target_email: str | None = None
) -> tuple[Any, str]:
    async with AsyncSession(engine, expire_on_commit=False) as session:
        trainer = User(
            email="trainer@x.com",
            password_hash="!",
            role=UserRole.trainer,
            status=UserStatus.active,
            is_email_verified=True,
        )
        session.add(trainer)
        await session.flush()
        session.add(TrainerProfile(user_id=trainer.id, business_name="Academy", slug="academy"))
        await session.flush()
        coach = kind is ShareLinkKind.coach_unique
        link = ShareLink(
            code=f"code-{new_id()}",
            trainer_id=trainer.id,
            created_by_user_id=trainer.id,
            kind=kind,
            target_email=target_email,
            max_uses=1 if coach else None,
            expires_at=clock.now() + timedelta(days=7) if coach else None,
        )
        session.add(link)
        await session.commit()
    return trainer.id, link.code


async def _register(client: AsyncClient, code: str, email: str) -> Any:
    return await client.post(
        f"{REGISTER}?share_code={code}",
        json={
            "email": email,
            "password": STRONG,
            "first_name": "Ada",
            "last_name": "Lovelace",
        },
    )


@pytest.mark.gate
@pytest.mark.concurrency
async def test_20_way_coach_unique_redemption_one_success(
    committed_app: FastAPI,
    committed_client: AsyncClient,
    committed_engine: AsyncEngine,
    clock: FakeClock,
) -> None:
    committed_app.state.rate_limiter = _NoLimit()
    trainer_id, code = await _seed_link(
        committed_engine, clock, kind=ShareLinkKind.coach_unique, target_email="c@x.com"
    )

    results = await asyncio.gather(
        *[_register(committed_client, code, "c@x.com") for _ in range(20)]
    )

    assert sorted(r.status_code for r in results) == [201] + [404] * 19
    assert {r.json()["error"]["code"] for r in results if r.status_code == 404} == {
        "SHARE_LINK_INVALID"
    }
    async with AsyncSession(committed_engine) as session:
        assert (await session.execute(select(func.count()).select_from(CoachProfile))).scalar() == 1
        link = (await session.execute(select(ShareLink))).scalar_one()
        assert link.use_count == 1
        tenure = (await session.execute(select(CoachProfile))).scalar_one()
        assert tenure.trainer_id == trainer_id
        assert tenure.is_active is True


@pytest.mark.concurrency
async def test_200_way_static_registration_no_lost_writes(
    committed_app: FastAPI,
    committed_client: AsyncClient,
    committed_engine: AsyncEngine,
    clock: FakeClock,
) -> None:
    committed_app.state.rate_limiter = _NoLimit()
    _, code = await _seed_link(committed_engine, clock, kind=ShareLinkKind.player_static)

    # The test engine has no pool (one connection per request) and Postgres allows 100, so
    # cap the in-flight requests; 50 at a time still contend on the one link row.
    gate = asyncio.Semaphore(50)

    async def one(i: int) -> Any:
        async with gate:
            return await _register(committed_client, code, f"p{i}@x.com")

    results = await asyncio.gather(*[one(i) for i in range(200)])

    assert [r.status_code for r in results] == [201] * 200
    async with AsyncSession(committed_engine) as session:
        players = select(func.count()).select_from(User).where(User.role == UserRole.player)
        assert (await session.execute(players)).scalar() == 200
        assert (await session.execute(select(ShareLink.use_count))).scalar_one() == 200
        links = select(func.count()).select_from(TrainerPlayerLink)
        assert (await session.execute(links)).scalar() == 200
