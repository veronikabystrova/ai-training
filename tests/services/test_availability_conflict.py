from datetime import time

import pytest

from app.repositories.availability import WindowSpec
from app.services.availability_service import check_conflict


def window(start: str, end: str, weekday: int = 0) -> WindowSpec:
    return WindowSpec(
        weekday=weekday, start_time=time.fromisoformat(start), end_time=time.fromisoformat(end)
    )


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        (("09:00", "10:00"), ("10:00", "11:00"), False),  # touching after
        (("09:00", "10:00"), ("09:59", "11:00"), True),  # one minute of overlap
        (("09:00", "10:00"), ("08:00", "09:00"), False),  # touching before
        (("09:00", "10:00"), ("09:00", "10:00"), True),  # identical
        (("09:00", "12:00"), ("10:00", "11:00"), True),  # contained
        (("10:00", "11:00"), ("09:00", "12:00"), True),  # containing
    ],
)
def test_check_conflict_half_open(a: tuple[str, str], b: tuple[str, str], expected: bool) -> None:
    assert check_conflict([window(*a)], window(*b)) is expected


def test_check_conflict_other_weekday_never_conflicts() -> None:
    assert (
        check_conflict([window("09:00", "10:00", weekday=0)], window("09:00", "10:00", 1)) is False
    )


def test_check_conflict_any_of_many_and_empty() -> None:
    existing = [window("08:00", "09:00"), window("13:00", "14:00")]
    assert check_conflict(existing, window("13:30", "15:00")) is True
    assert check_conflict(existing, window("09:00", "13:00")) is False
    assert check_conflict([], window("09:00", "10:00")) is False
