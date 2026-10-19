from collections.abc import Collection
from datetime import datetime
from uuid import UUID

from sqlalchemy import Integer, cast, delete, extract, func, or_, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.identity import (
    AvailabilityWindow,
    CoachProfile,
    LinkStatus,
    OneTimeToken,
    PlayerProfile,
    ShareLink,
    TrainerPlayerLink,
    TrainerProfile,
    UserErasure,
)
from app.services.erasure import ERASED_PLAYER_NAME


class UserErasureRepository:
    """The erasure ledger plus the bulk scrubs of one erasure (US-01.13).

    The scrubs are keyed by the erased user or by the personal data itself, not by tenant:
    they are an admin-only system operation, so no ``trainer_id`` applies.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def append(
        self,
        *,
        original_user_id: UUID,
        original_email_hash: str,
        erased_by_user_id: UUID,
        reason: str | None,
        legal_retention_until: datetime,
    ) -> UserErasure:
        row = UserErasure(
            original_user_id=original_user_id,
            original_email_hash=original_email_hash,
            erased_by_user_id=erased_by_user_id,
            reason=reason,
            legal_retention_until=legal_retention_until,
        )
        self._s.add(row)
        await self._s.flush()
        return row

    async def scrub_owned_profiles(self, user_id: UUID) -> list[tuple[UUID, UUID | None]]:
        """Anonymise every player profile the user owns or logs in to, and retire it.

        A child login owns nothing (``owner_user_id`` is the parent) but its profile is its own
        personal data, so ``login_user_id`` matches too.

        Returns ``(profile_id, login_user_id)`` for each. ``birth_date`` is NOT NULL, so it
        is generalised to January 1 of the birth year.
        """
        result = await self._s.execute(
            update(PlayerProfile)
            .where(
                or_(PlayerProfile.owner_user_id == user_id, PlayerProfile.login_user_id == user_id)
            )
            .values(
                display_name=ERASED_PLAYER_NAME,
                school=None,
                skill_level=None,
                jersey_number=None,
                birth_date=func.make_date(
                    cast(extract("year", PlayerProfile.birth_date), Integer), 1, 1
                ),
                emergency_contact_name=None,
                emergency_contact_phone=None,
                is_active=False,
            )
            .returning(PlayerProfile.id, PlayerProfile.login_user_id)
            .execution_options(synchronize_session=False)
        )
        return [(row.id, row.login_user_id) for row in result.all()]

    async def deactivate_links(self, player_profile_ids: Collection[UUID]) -> None:
        await self._s.execute(
            update(TrainerPlayerLink)
            .where(TrainerPlayerLink.player_profile_id.in_(player_profile_ids))
            .values(status=LinkStatus.inactive)
            .execution_options(synchronize_session=False)
        )

    async def delete_availability(self, subject_ids: Collection[UUID]) -> None:
        await self._s.execute(
            delete(AvailabilityWindow)
            .where(AvailabilityWindow.subject_id.in_(subject_ids))
            .execution_options(synchronize_session=False)
        )

    async def delete_one_time_tokens(self, user_ids: Collection[UUID]) -> None:
        """Pending tokens may carry the new address (email change): they go with the user."""
        await self._s.execute(
            delete(OneTimeToken)
            .where(OneTimeToken.user_id.in_(user_ids))
            .execution_options(synchronize_session=False)
        )

    async def scrub_share_link_email(self, email: str, *, placeholder: str) -> None:
        """Replace the address on links issued to it and deactivate them.

        ``target_email`` is CITEXT, so the match ignores case. The column is NOT NULL for
        ``coach_unique`` links (``ck_share_links_kind_fields``), hence a placeholder.
        """
        await self._s.execute(
            update(ShareLink)
            .where(ShareLink.target_email == email)
            .values(target_email=placeholder, is_active=False)
            .execution_options(synchronize_session=False)
        )

    async def end_coach_tenures(self, user_id: UUID, *, now: datetime) -> None:
        """An erased coach is a former coach: ``is_active`` and ``left_at`` change together."""
        await self._s.execute(
            update(CoachProfile)
            .where(CoachProfile.user_id == user_id, CoachProfile.is_active)
            .values(is_active=False, left_at=now)
            .execution_options(synchronize_session=False)
        )

    async def scrub_trainer_profile(self, trainer_id: UUID) -> None:
        await self._s.execute(
            update(TrainerProfile)
            .where(TrainerProfile.user_id == trainer_id)
            .values(address=None, website=None, description=None, logo_key=None)
            .execution_options(synchronize_session=False)
        )
