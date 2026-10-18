from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from redis import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer

from tests.fakes import FakeClock, FakeEmailSender, FakeObjectStorage

pytest_plugins = ["tests.factories", "tests.app_fixtures"]


@pytest.fixture(scope="session")
def pg_container() -> Iterator[PostgresContainer]:
    with PostgresContainer("postgres:16", driver=None) as pg:
        yield pg


@pytest.fixture(scope="session")
def redis_container() -> Iterator[RedisContainer]:
    with RedisContainer("redis:7") as rc:
        yield rc


@pytest.fixture(scope="session")
def database_url(pg_container: PostgresContainer) -> str:
    """Async DSN of a Postgres migrated to head once per test session."""
    dsn: str = pg_container.get_connection_url()
    url = dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
    cfg = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.upgrade(cfg, "head")
    return url


@pytest.fixture
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    # NullPool: asyncpg connections must not outlive the per-test event loop.
    eng = create_async_engine(database_url, poolclass=NullPool)
    yield eng
    await eng.dispose()


@pytest.fixture
async def db_session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """Session inside an outer transaction that is rolled back after the test."""
    async with engine.connect() as conn:
        outer = await conn.begin()
        session = AsyncSession(
            bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False
        )
        try:
            yield session
        finally:
            await session.close()
            await outer.rollback()


@pytest.fixture
async def committed_engine(engine: AsyncEngine) -> AsyncIterator[AsyncEngine]:
    """For @pytest.mark.concurrency tests: real commits, tables truncated afterwards."""
    yield engine
    async with engine.begin() as conn:
        tables = await conn.execute(
            text(
                "select tablename from pg_tables "
                "where schemaname='public' and tablename <> 'alembic_version'"
            )
        )
        names = ", ".join(f'"{r[0]}"' for r in tables)
        if names:
            await conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))


@pytest.fixture
def redis_url(redis_container: RedisContainer) -> str:
    host = redis_container.get_container_host_ip()
    port = redis_container.get_exposed_port(6379)
    with Redis(host=host, port=int(port)) as client:
        client.flushall()
    return f"redis://{host}:{port}"


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(datetime(2026, 10, 18, 10, 0, tzinfo=UTC))


@pytest.fixture
def email() -> FakeEmailSender:
    return FakeEmailSender()


@pytest.fixture
def storage() -> FakeObjectStorage:
    return FakeObjectStorage()
