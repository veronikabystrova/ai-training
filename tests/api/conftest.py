from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.main import create_app
from tests.api.probes import router as probe_router
from tests.factories import TEST_JWT_SECRET
from tests.fakes import FakeClock


@pytest.fixture
def settings(database_url: str, redis_url: str) -> Settings:
    return Settings(
        database_url=database_url,
        redis_url=redis_url,
        jwt_secret=TEST_JWT_SECRET,
        hash_salt="s" * 16,
    )


@pytest.fixture
def app(settings: Settings, clock: FakeClock, db_session: AsyncSession) -> FastAPI:
    """The real app (middleware, error handlers, routers) plus probe routes.

    Every session, in dependencies and middleware alike, is the one rolled-back test session.
    """
    application = create_app(settings, clock=clock)

    @asynccontextmanager
    async def shared_session() -> AsyncIterator[AsyncSession]:
        yield db_session

    application.state.session_factory = shared_session
    application.state.effects = []
    application.include_router(probe_router)
    return application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c
