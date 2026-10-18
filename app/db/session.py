from collections.abc import AsyncIterator
from typing import Any, cast

from sqlalchemy import CursorResult, Result
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def create_engine(url: str) -> AsyncEngine:
    return create_async_engine(url, pool_pre_ping=True)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_session(factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    """Yield one AsyncSession per request. Services own commit/rollback; we only close."""
    async with factory() as session:
        yield session


def rowcount(result: Result[Any]) -> int:
    """Rows matched by an UPDATE/DELETE (``Result`` is typed without ``rowcount``)."""
    return cast("CursorResult[Any]", result).rowcount
