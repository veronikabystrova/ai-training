from datetime import time

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import constraint_name
from app.repositories.availability import AvailabilityRepository, WindowSpec
from tests.factories import AddWindow, MakePlayer


async def test_replace_swaps_the_whole_set(
    db_session: AsyncSession, make_player: MakePlayer, add_window: AddWindow
) -> None:
    repo = AvailabilityRepository(db_session)
    p = await make_player()
    await add_window(p.id, weekday=0, start="09:00", end="10:00")
    await repo.replace(
        subject_type="player",
        subject_id=p.id,
        windows=[
            WindowSpec(weekday=0, start_time=time(9), end_time=time(10)),  # same slot re-sent
            WindowSpec(weekday=2, start_time=time(15), end_time=time(16, 30)),
        ],
    )
    rows = await repo.get_by_subject(subject_type="player", subject_id=p.id)
    assert [(r.weekday, r.start_time, r.end_time) for r in rows] == [
        (0, time(9), time(10)),
        (2, time(15), time(16, 30)),
    ]


async def test_replace_with_empty_clears_and_is_subject_scoped(
    db_session: AsyncSession, make_player: MakePlayer, add_window: AddWindow
) -> None:
    repo = AvailabilityRepository(db_session)
    a, b = await make_player(), await make_player()
    await add_window(a.id, weekday=1, start="09:00", end="10:00")
    await add_window(b.id, weekday=1, start="09:00", end="10:00")
    await repo.replace(subject_type="player", subject_id=a.id, windows=[])
    assert await repo.get_by_subject(subject_type="player", subject_id=a.id) == []
    assert len(await repo.get_by_subject(subject_type="player", subject_id=b.id)) == 1


async def test_replace_overlap_raises_exclusion_constraint(
    db_session: AsyncSession, make_player: MakePlayer
) -> None:
    repo = AvailabilityRepository(db_session)
    p = await make_player()
    with pytest.raises(IntegrityError) as e:
        await repo.replace(
            subject_type="player",
            subject_id=p.id,
            windows=[
                WindowSpec(weekday=0, start_time=time(9), end_time=time(11)),
                WindowSpec(weekday=0, start_time=time(10), end_time=time(12)),
            ],
        )
    assert constraint_name(e.value) == "ex_availability_no_overlap"
