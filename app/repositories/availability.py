from dataclasses import dataclass
from datetime import time
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.identity import AvailabilityWindow, SubjectType


@dataclass(frozen=True)
class WindowSpec:
    weekday: int
    start_time: time
    end_time: time
    is_available: bool = True


class AvailabilityRepository:
    """No ``trainer_id`` (G-10): staff reads must go through an active trainer-player link."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get_by_subject(
        self, *, subject_type: SubjectType, subject_id: UUID
    ) -> list[AvailabilityWindow]:
        result = await self._s.execute(
            select(AvailabilityWindow)
            .where(
                AvailabilityWindow.subject_type == subject_type,
                AvailabilityWindow.subject_id == subject_id,
            )
            .order_by(AvailabilityWindow.weekday, AvailabilityWindow.start_time)
        )
        return list(result.scalars())

    async def replace(
        self, *, subject_type: SubjectType, subject_id: UUID, windows: list[WindowSpec]
    ) -> list[AvailabilityWindow]:
        """Delete the subject's set and insert the new one in a single flush.

        Overlapping windows raise ``IntegrityError`` (``ex_availability_no_overlap``); the
        caller's transaction decides whether the delete is rolled back.
        """
        await self._s.execute(
            delete(AvailabilityWindow)
            .where(
                AvailabilityWindow.subject_type == subject_type,
                AvailabilityWindow.subject_id == subject_id,
            )
            .execution_options(synchronize_session="fetch")
        )
        rows = [
            AvailabilityWindow(
                subject_type=subject_type,
                subject_id=subject_id,
                weekday=w.weekday,
                start_time=w.start_time,
                end_time=w.end_time,
                is_available=w.is_available,
            )
            for w in windows
        ]
        self._s.add_all(rows)
        await self._s.flush()
        return sorted(rows, key=lambda r: (r.weekday, r.start_time))
