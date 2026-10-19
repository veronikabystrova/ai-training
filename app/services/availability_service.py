"""Weekly availability of a player profile or a coach.

``check_conflict`` is the pure overlap rule (half-open ``[start, end)``, same as the GiST
exclusion constraint). ``AvailabilityService.replace`` leaves the decision to that
constraint: the delete and the insert share one savepoint, so an overlap restores the old set.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, UnauthorizedError, map_integrity_error
from app.models.identity import AvailabilityWindow, Profile, SubjectType, UserRole
from app.repositories.availability import AvailabilityRepository, WindowSpec
from app.repositories.player import PlayerProfileRepository
from app.repositories.profile import ProfileRepository


def check_conflict(existing: Sequence[WindowSpec], candidate: WindowSpec) -> bool:
    """True when ``candidate`` overlaps any ``existing`` window on the same weekday.

    Windows are half-open: 09:00-10:00 and 10:00-11:00 touch but do not conflict.
    """
    return any(
        w.weekday == candidate.weekday
        and w.start_time < candidate.end_time
        and candidate.start_time < w.end_time
        for w in existing
    )


@dataclass(frozen=True, slots=True)
class AvailabilityView:
    timezone: str
    windows: list[AvailabilityWindow]


@dataclass(frozen=True, slots=True)
class _Subject:
    subject_type: SubjectType
    subject_id: UUID
    timezone_user_id: UUID  # whose ``Profile.timezone`` the wall-clock times are in


class AvailabilityService:
    def __init__(self, *, session: AsyncSession) -> None:
        self._session = session
        self._windows = AvailabilityRepository(session)
        self._profiles = ProfileRepository(session)
        self._players = PlayerProfileRepository(session)

    async def get_own(self, *, user_id: UUID, role: UserRole, child: bool) -> AvailabilityView:
        subject = await self._own_subject(user_id=user_id, role=role, child=child)
        view = await self._view(subject)
        await self._session.commit()  # end the read transaction
        return view

    async def replace_own(
        self,
        *,
        user_id: UUID,
        role: UserRole,
        child: bool,
        timezone: str,
        windows: list[WindowSpec],
    ) -> AvailabilityView:
        subject = await self._own_subject(user_id=user_id, role=role, child=child)
        return await self._replace(subject, timezone, windows)

    async def replace_for_dependent(
        self,
        *,
        owner_user_id: UUID,
        player_profile_id: UUID,
        timezone: str,
        windows: list[WindowSpec],
    ) -> AvailabilityView:
        player = await self._players.get_owned(owner_user_id=owner_user_id, id=player_profile_id)
        if player is None:
            raise NotFoundError()
        subject = _Subject("player", player.id, timezone_user_id=owner_user_id)
        return await self._replace(subject, timezone, windows)

    # --- internals -------------------------------------------------------------------------

    async def _own_subject(self, *, user_id: UUID, role: UserRole, child: bool) -> _Subject:
        if role is UserRole.coach:
            return _Subject("coach", user_id, timezone_user_id=user_id)
        if role is not UserRole.player:
            raise NotFoundError()  # routes gate the role first; never reached by a staff caller
        if child:
            profile = await self._players.get_by_login_user(user_id)
        else:
            profile = await self._players.get_own(user_id)
        if profile is None:
            raise NotFoundError()
        return _Subject("player", profile.id, timezone_user_id=user_id)

    async def _profile(self, user_id: UUID) -> Profile:
        profile = await self._profiles.get(user_id)
        if profile is None:
            raise UnauthorizedError()
        return profile

    async def _view(self, subject: _Subject) -> AvailabilityView:
        profile = await self._profile(subject.timezone_user_id)
        rows = await self._windows.get_by_subject(
            subject_type=subject.subject_type, subject_id=subject.subject_id
        )
        return AvailabilityView(timezone=profile.timezone, windows=rows)

    async def _replace(
        self, subject: _Subject, timezone: str, windows: list[WindowSpec]
    ) -> AvailabilityView:
        profile = await self._profile(subject.timezone_user_id)
        try:
            async with self._session.begin_nested():
                profile.timezone = timezone
                rows = await self._windows.replace(
                    subject_type=subject.subject_type,
                    subject_id=subject.subject_id,
                    windows=windows,
                )
        except IntegrityError as exc:
            mapped = map_integrity_error(exc)
            if mapped is None:
                raise
            raise mapped from exc
        await self._session.commit()
        return AvailabilityView(timezone=timezone, windows=rows)
