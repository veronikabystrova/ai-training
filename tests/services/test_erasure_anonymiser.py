import hashlib
from datetime import UTC, datetime
from uuid import UUID

from app.services.erasure import (
    ERASED_PLAYER_NAME,
    email_fingerprint,
    erased_email,
    retention_until,
)

USER_ID = UUID("0192f1b3-0000-7000-8000-000000000001")


def test_erased_email_is_derived_from_the_id_and_carries_no_original_data() -> None:
    assert erased_email(USER_ID) == f"deleted-{USER_ID}@erased.invalid"
    assert erased_email(USER_ID) != erased_email(UUID(int=USER_ID.int + 1))  # users.email is unique


def test_email_fingerprint_is_the_sha256_of_the_lowercased_address() -> None:
    expected = hashlib.sha256(b"sam@example.com").hexdigest()
    assert email_fingerprint("Sam@Example.com") == expected
    assert email_fingerprint("sam@example.com") == expected
    assert len(expected) == 64  # fits user_erasures.original_email_hash (String(64))
    assert "sam" not in expected


def test_retention_adds_whole_calendar_years() -> None:
    erased = datetime(2026, 10, 19, 8, 30, tzinfo=UTC)
    assert retention_until(erased, years=7) == datetime(2033, 10, 19, 8, 30, tzinfo=UTC)
    assert retention_until(erased, years=0) == erased


def test_retention_from_a_leap_day_lands_on_feb_28_in_a_common_year() -> None:
    leap = datetime(2028, 2, 29, 12, 0, tzinfo=UTC)
    assert retention_until(leap, years=1) == datetime(2029, 2, 28, 12, 0, tzinfo=UTC)
    assert retention_until(leap, years=4) == datetime(2032, 2, 29, 12, 0, tzinfo=UTC)


def test_erased_player_name_is_generic() -> None:
    assert ERASED_PLAYER_NAME == "Deleted Player"
