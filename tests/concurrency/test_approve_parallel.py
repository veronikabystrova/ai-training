import asyncio
from datetime import timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.ids import new_id
from app.models.identity import (
    ApprovalStatus,
    Gender,
    PlayerProfile,
    Profile,
    PurchaseApproval,
    TrainerProfile,
    User,
    UserRole,
    UserStatus,
)
from tests.api.helpers import bearer
from tests.factories import PrincipalToken
from tests.fakes import FakeClock, FakePurchaseExecutor


def _user(email: str, role: UserRole) -> User:
    return User(
        email=email,
        password_hash="!",
        role=role,
        status=UserStatus.active,
        is_email_verified=True,
        profile=Profile(first_name="T", last_name="U"),
    )


async def _seed(engine: AsyncEngine, clock: FakeClock) -> tuple[User, PurchaseApproval]:
    async with AsyncSession(engine, expire_on_commit=False) as session:
        parent = _user("parent@x.com", UserRole.player)
        trainer = _user("trainer@x.com", UserRole.trainer)
        session.add_all([parent, trainer])
        await session.flush()
        session.add(TrainerProfile(user_id=trainer.id, business_name="Academy", slug="academy"))
        child = PlayerProfile(
            owner_user_id=parent.id,
            display_name="Mia",
            birth_date=clock.today().replace(year=clock.today().year - 10),
            gender=Gender.undisclosed,
            is_dependent=True,
        )
        session.add(child)
        await session.flush()
        approval = PurchaseApproval(
            player_profile_id=child.id,
            parent_user_id=parent.id,
            trainer_id=trainer.id,
            subject_type="pack",
            subject_id=new_id(),
            amount_cents=500,
            expires_at=clock.now() + timedelta(hours=48),
        )
        session.add(approval)
        await session.commit()
    return parent, approval


@pytest.mark.gate
@pytest.mark.concurrency
async def test_10_way_parallel_approve_one_effect(
    committed_client: AsyncClient,
    committed_engine: AsyncEngine,
    executor: FakePurchaseExecutor,
    clock: FakeClock,
    principal_token: PrincipalToken,
) -> None:
    parent, approval = await _seed(committed_engine, clock)
    headers = bearer(principal_token(parent))

    async def approve(i: int) -> Any:
        return await committed_client.post(
            f"/api/v1/me/approvals/{approval.id}/approve",
            headers={**headers, "Idempotency-Key": f"k{i}"},
        )

    results = await asyncio.gather(*[approve(i) for i in range(10)])

    codes = sorted(r.status_code for r in results)
    assert codes == [200] + [409] * 9
    assert len(executor.executed) == 1
    async with AsyncSession(committed_engine) as session:
        row = (await session.execute(select(PurchaseApproval))).scalar_one()
        assert row.status is ApprovalStatus.approved


@pytest.mark.concurrency
async def test_parallel_approve_same_key_replays_one_effect(
    committed_client: AsyncClient,
    committed_engine: AsyncEngine,
    executor: FakePurchaseExecutor,
    clock: FakeClock,
    principal_token: PrincipalToken,
) -> None:
    parent, approval = await _seed(committed_engine, clock)
    headers = {**bearer(principal_token(parent)), "Idempotency-Key": "same"}

    results = await asyncio.gather(
        *[
            committed_client.post(f"/api/v1/me/approvals/{approval.id}/approve", headers=headers)
            for _ in range(10)
        ]
    )

    assert [r.status_code for r in results] == [200] * 10
    assert len({r.text for r in results}) == 1
    assert len(executor.executed) == 1
