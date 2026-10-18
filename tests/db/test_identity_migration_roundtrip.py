import asyncio
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from testcontainers.postgres import PostgresContainer

_DB = "roundtrip"


async def _table_names(url: str) -> set[str]:
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            rows = await conn.execute(
                text("select tablename from pg_tables where schemaname = 'public'")
            )
            return {r[0] for r in rows}
    finally:
        await engine.dispose()


@pytest.mark.parametrize("base", ["0001", "base"])
async def test_0002_downgrade_and_reupgrade(pg_container: PostgresContainer, base: str) -> None:
    """``head -> <base> -> head`` on a scratch database (the shared one stays at head)."""
    admin = make_url(pg_container.get_connection_url()).set(drivername="postgresql+asyncpg")
    root = create_async_engine(admin, poolclass=NullPool, isolation_level="AUTOCOMMIT")
    try:
        async with root.connect() as conn:
            await conn.execute(text(f"DROP DATABASE IF EXISTS {_DB}"))
            await conn.execute(text(f"CREATE DATABASE {_DB}"))
    finally:
        await root.dispose()
    url = admin.set(database=_DB).render_as_string(hide_password=False)
    cfg = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))

    await asyncio.to_thread(command.upgrade, cfg, "head")
    assert "users" in await _table_names(url)
    await asyncio.to_thread(command.downgrade, cfg, base)
    remaining = await _table_names(url)
    assert "users" not in remaining and "impersonation_sessions" not in remaining
    await asyncio.to_thread(command.upgrade, cfg, "head")
    assert "impersonation_sessions" in await _table_names(url)
