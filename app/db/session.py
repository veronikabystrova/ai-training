from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any, cast

from sqlalchemy import CursorResult, Result
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# ``async_sessionmaker`` satisfies this; tests substitute a factory yielding one shared session.
SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


def create_engine(url: str) -> AsyncEngine:
    # hide_parameters: str() of a DB error must not carry bound values (emails, hashes) into logs.
    return create_async_engine(url, pool_pre_ping=True, hide_parameters=True)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


def rowcount(result: Result[Any]) -> int:
    """Rows matched by an UPDATE/DELETE (``Result`` is typed without ``rowcount``)."""
    return cast("CursorResult[Any]", result).rowcount
