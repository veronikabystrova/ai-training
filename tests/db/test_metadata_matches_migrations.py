from typing import Any

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine

import app.models  # noqa: F401  (register models)
from app.db.base import Base
from app.db.migration_filters import include_object


async def test_models_match_migrations(engine: AsyncEngine, database_url: str) -> None:
    """After ``upgrade head`` autogenerate must propose nothing (no drift)."""

    def _diff(sync_conn: Connection) -> list[Any]:
        ctx = MigrationContext.configure(
            sync_conn,
            opts={
                "compare_type": True,
                "compare_server_default": True,
                "include_object": include_object,
            },
        )
        diff: list[Any] = compare_metadata(ctx, Base.metadata)
        return diff

    async with engine.connect() as conn:
        diff = await conn.run_sync(_diff)
    assert diff == []
