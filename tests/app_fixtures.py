"""App-level fixtures shared by ``tests/api`` and the service/concurrency suites."""

from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings
from app.db.session import create_session_factory
from app.main import create_app
from tests.factories import TEST_JWT_SECRET
from tests.fakes import FakeClock, FakeCommitmentChecker, FakeJobQueue, FakePurchaseExecutor


@pytest.fixture
def settings(database_url: str, redis_url: str) -> Settings:
    return Settings(
        database_url=database_url,
        redis_url=redis_url,
        jwt_secret=TEST_JWT_SECRET,
        hash_salt="s" * 16,
        # Cheap argon2, identical to the factories' hashes, so logins do not trigger a rehash.
        argon2_time_cost=1,
        argon2_memory_kib=8,
        argon2_parallelism=1,
    )


@pytest.fixture
def jobs() -> FakeJobQueue:
    return FakeJobQueue()


@pytest.fixture
def commitments() -> FakeCommitmentChecker:
    return FakeCommitmentChecker()


@pytest.fixture
def executor() -> FakePurchaseExecutor:
    return FakePurchaseExecutor()


@pytest.fixture
def committed_app(
    settings: Settings,
    clock: FakeClock,
    committed_engine: AsyncEngine,
    jobs: FakeJobQueue,
    commitments: FakeCommitmentChecker,
    executor: FakePurchaseExecutor,
) -> FastAPI:
    """The real app over real connections: every request commits (``@pytest.mark.concurrency``)."""
    application = create_app(settings, clock=clock)
    application.state.session_factory = create_session_factory(committed_engine)
    application.state.job_queue = jobs
    application.state.commitment_checker = commitments
    application.state.purchase_executor = executor
    return application


@pytest.fixture
async def committed_client(committed_app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=committed_app), base_url="http://t") as c:
        yield c
