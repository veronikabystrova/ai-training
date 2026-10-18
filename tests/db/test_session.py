import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db.session import create_engine


async def test_engine_hides_bound_parameters_from_error_text(database_url: str) -> None:
    engine = create_engine(database_url)
    try:
        async with engine.connect() as conn:
            with pytest.raises(DBAPIError) as exc_info:
                await conn.execute(text("select cast(:marker as int) / 0"), {"marker": 4242424})
    finally:
        await engine.dispose()
    assert "division by zero" in str(exc_info.value)
    assert "4242424" not in str(exc_info.value)
    assert "[parameters: (" not in str(exc_info.value)
