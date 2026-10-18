from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.session import create_session_factory, get_session


async def test_get_session_yields_usable_session_then_closes(engine: AsyncEngine) -> None:
    gen = aiter(get_session(create_session_factory(engine)))
    session = await anext(gen)
    assert (await session.execute(text("select 1"))).scalar_one() == 1
    await session.execute(text("select 1"))
    assert session.in_transaction()
    await gen.aclose()  # type: ignore[attr-defined]
    assert not session.in_transaction()
