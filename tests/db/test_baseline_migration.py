import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession


async def test_baseline_extensions_and_range_type(db_session: AsyncSession) -> None:
    ext = {r[0] for r in await db_session.execute(text("select extname from pg_extension"))}
    assert {"citext", "btree_gist", "pg_trgm"} <= ext
    r = await db_session.execute(text("select 1 from pg_type where typname = 'timerange'"))
    assert r.scalar() == 1


async def test_baseline_tables_exist(db_session: AsyncSession) -> None:
    result = await db_session.execute(
        text("select tablename from pg_tables where schemaname='public'")
    )
    names = {r[0] for r in result}
    assert {"idempotency_keys", "audit_log", "feature_flags"} <= names


async def test_feature_flags_global_key_is_unique(db_session: AsyncSession) -> None:
    ins = text(
        "insert into feature_flags (id, key, trainer_id) values (gen_random_uuid(), 'x', NULL)"
    )
    await db_session.execute(ins)
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await db_session.execute(ins)
