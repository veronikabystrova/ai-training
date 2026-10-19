from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.once import RedisOnceStore
from app.jobs.identity_jobs import Ctx
from app.services.maintenance_service import MaintenanceService
from app.services.notification_service import NotificationService
from tests.fakes import FakeClock, FakeEmailSender


@pytest.fixture
async def ctx(
    db_session: AsyncSession,
    clock: FakeClock,
    email: FakeEmailSender,
    settings: Settings,
    redis_url: str,
) -> AsyncIterator[Ctx]:
    """Worker context over the rolled-back test session, a fake mailer and a real Redis."""

    @asynccontextmanager
    async def shared_session() -> AsyncIterator[AsyncSession]:
        yield db_session

    redis = Redis.from_url(redis_url)
    yield {
        "job_try": 1,
        "notifications": NotificationService(
            session_factory=shared_session,
            clock=clock,
            settings=settings,
            email=email,
            once=RedisOnceStore(redis),
        ),
        "maintenance": MaintenanceService(session_factory=shared_session, clock=clock),
    }
    await redis.aclose()
