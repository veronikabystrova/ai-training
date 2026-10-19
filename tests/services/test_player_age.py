from datetime import date

import pytest

from app.core.errors import UnprocessableError
from app.services.player_service import age_on, validate_dependent_age
from tests.fakes import FakeClock


@pytest.mark.parametrize(
    ("birth", "ok"),
    [
        ("2008-10-18", False),  # turns 18 today on FakeClock (2026-10-18) => rejected
        ("2008-10-19", True),  # 17, turns 18 tomorrow => allowed
        ("2025-10-18", True),  # exactly 1
        ("2025-10-19", False),  # not yet 1
        ("2026-10-18", False),  # born today
        ("2027-01-01", False),  # born in the future
        ("2015-02-28", True),
    ],
)
def test_dependent_age_rule_1_to_17(birth: str, ok: bool, clock: FakeClock) -> None:
    born = date.fromisoformat(birth)
    if ok:
        validate_dependent_age(born, clock.today())
        return
    with pytest.raises(UnprocessableError) as exc:
        validate_dependent_age(born, clock.today())
    assert exc.value.code == "INVALID_AGE"


@pytest.mark.parametrize(
    ("birth", "today", "age"),
    [
        (date(2008, 2, 29), date(2026, 2, 28), 17),  # leap-day birthday, not yet reached
        (date(2008, 2, 29), date(2026, 3, 1), 18),
        (date(2020, 12, 31), date(2026, 12, 30), 5),
        (date(2020, 12, 31), date(2026, 12, 31), 6),
    ],
)
def test_age_on_birthday_boundaries(birth: date, today: date, age: int) -> None:
    assert age_on(birth, today) == age
