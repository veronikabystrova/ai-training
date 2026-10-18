import base64
import json
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import Query
from pydantic import BaseModel

from app.core.errors import UnprocessableError


class Page[T](BaseModel):
    items: list[T]
    next_cursor: str | None = None


LimitQ = Annotated[int, Query(ge=1, le=100)]
CursorQ = Annotated[str | None, Query(max_length=256)]


def encode_cursor(ts: datetime, ident: UUID) -> str:
    raw = json.dumps([ts.isoformat(), str(ident)]).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        ts, ident = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        parsed = datetime.fromisoformat(ts)
        if parsed.tzinfo is None:
            raise ValueError("cursor timestamp must be timezone-aware")
        return parsed, UUID(ident)
    except (ValueError, TypeError) as exc:
        raise UnprocessableError("VALIDATION_ERROR", "Invalid cursor") from exc
