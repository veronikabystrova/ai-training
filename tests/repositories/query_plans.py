"""Helper: run a repository call, then EXPLAIN the SELECT it issued with seq scans disabled.

Test tables are tiny, so the planner would seq-scan regardless. Disabling seq scans turns
"is the index usable for this predicate?" into a deterministic assertion on the plan text.
"""

from collections.abc import Awaitable
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.engine.interfaces import DBAPICursor, ExecutionContext
from sqlalchemy.ext.asyncio import AsyncSession


async def explain_select(session: AsyncSession, call: Awaitable[object]) -> str:
    """Await ``call`` and return the plan of the last SELECT it executed."""
    captured: list[tuple[str, Any]] = []

    def _capture(
        _conn: object,
        _cursor: DBAPICursor,
        statement: str,
        parameters: Any,
        _context: ExecutionContext | None,
        _many: bool,
    ) -> None:
        if statement.lstrip().upper().startswith("SELECT"):
            captured.append((statement, parameters))

    connection = await session.connection()
    sync_engine = connection.sync_engine
    event.listen(sync_engine, "before_cursor_execute", _capture)
    try:
        await call
    finally:
        event.remove(sync_engine, "before_cursor_execute", _capture)
    statement, parameters = captured[-1]

    await session.execute(text("SELECT set_config('enable_seqscan', 'off', true)"))
    try:
        result = await connection.exec_driver_sql(f"EXPLAIN {statement}", parameters)
        return "\n".join(str(row[0]) for row in result)
    finally:
        await session.execute(text("SELECT set_config('enable_seqscan', 'on', true)"))
