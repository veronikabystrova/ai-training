from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.core.clock import SystemClock
from app.core.config import Settings
from app.core.ids import new_id
from tests.fakes import FakeClock


def test_new_id_is_uuid7_and_time_ordered() -> None:
    a, b = new_id(), new_id()
    assert isinstance(a, UUID) and a.version == 7
    assert a < b


def test_fake_clock_advances() -> None:
    clock = FakeClock(datetime(2026, 10, 18, tzinfo=UTC))
    clock.advance(minutes=15)
    assert clock.now() == datetime(2026, 10, 18, 0, 15, tzinfo=UTC)
    assert clock.today().isoformat() == "2026-10-18"


def test_system_clock_is_tz_aware() -> None:
    assert SystemClock().now().tzinfo is not None


def test_settings_defaults() -> None:
    s = Settings(
        jwt_secret="x" * 32,
        hash_salt="s" * 16,
        database_url="postgresql+asyncpg://u:p@h/db",
        redis_url="redis://h",
    )
    assert s.access_ttl_seconds == 900
    assert s.refresh_ttl_days == 30
    assert s.impersonation_max_minutes == 60
    assert s.erasure_retention_years == 7


def test_settings_reject_short_secrets_and_hide_them() -> None:
    db, redis = "postgresql+asyncpg://u:p@h/db", "redis://h"
    with pytest.raises(ValidationError):
        Settings(database_url=db, redis_url=redis, jwt_secret="k", hash_salt="s" * 16)
    with pytest.raises(ValidationError):
        Settings(database_url=db, redis_url=redis, jwt_secret="x" * 32)  # hash_salt required
    s = Settings(database_url=db, redis_url=redis, jwt_secret="x" * 32, hash_salt="s" * 16)
    assert "x" * 32 not in repr(s)
    assert s.jwt_secret.get_secret_value() == "x" * 32
