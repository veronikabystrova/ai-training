import base64
import json
from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.core.errors import UnprocessableError
from app.core.pagination import Page, decode_cursor, encode_cursor


def test_cursor_roundtrip() -> None:
    ts, ident = datetime(2026, 10, 18, tzinfo=UTC), UUID(int=7)
    assert decode_cursor(encode_cursor(ts, ident)) == (ts, ident)


def test_garbage_cursor_is_422() -> None:
    with pytest.raises(UnprocessableError):
        decode_cursor("not-a-cursor")


def test_page_envelope() -> None:
    assert Page[int](items=[1]).model_dump() == {"items": [1], "next_cursor": None}


def test_naive_datetime_cursor_is_422() -> None:
    raw = json.dumps(["2026-01-01T00:00:00", str(UUID(int=1))]).encode()
    cursor = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    with pytest.raises(UnprocessableError):
        decode_cursor(cursor)
