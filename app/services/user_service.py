"""The caller's own account: profile, avatar and email change.

The service owns the transaction (repositories never commit). Reads end without a pending
transaction; every write commits once.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import Clock
from app.core.config import Settings
from app.core.errors import (
    EmailTakenError,
    ForbiddenError,
    NotFoundError,
    UnauthorizedError,
    UnprocessableError,
    map_integrity_error,
    token_invalid,
)
from app.core.ids import new_id
from app.core.jobs import JobQueue, enqueue_best_effort
from app.core.media import MediaUrlBuilder
from app.core.security import PasswordHasher
from app.core.storage import ObjectStorage, PresignedPut
from app.models.identity import CoachProfile, OttPurpose, Profile, User, UserRole, UserStatus
from app.repositories.coach_profile import CoachProfileRepository
from app.repositories.one_time_token import OneTimeTokenRepository
from app.repositories.profile import ProfileRepository
from app.repositories.refresh_token import RefreshTokenRepository
from app.repositories.user import UserRepository
from app.services.audit_service import AuditService

AVATAR_CONTENT_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})
AVATAR_MAX_BYTES = 5 * 1024 * 1024

PROFILE_FIELDS = frozenset({"first_name", "last_name", "phone", "school", "timezone"})
COACH_FIELDS = frozenset({"bio", "credentials", "certifications", "is_public"})


@dataclass(frozen=True, slots=True)
class MeView:
    user: User
    profile: Profile
    coach: CoachProfile | None
    avatar_url: str | None


class UserService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        settings: Settings,
        clock: Clock,
        hasher: PasswordHasher,
        storage: ObjectStorage,
        audit: AuditService,
        jobs: JobQueue,
    ) -> None:
        self._session = session
        self._settings = settings
        self._clock = clock
        self._hasher = hasher
        self._storage = storage
        self._audit = audit
        self._jobs = jobs
        self._media = MediaUrlBuilder(settings.media_base_url)
        self._users = UserRepository(session)
        self._profiles = ProfileRepository(session)
        self._coaches = CoachProfileRepository(session)
        self._tokens = OneTimeTokenRepository(session)
        self._refresh_tokens = RefreshTokenRepository(session)

    # --- profile ---------------------------------------------------------------------------

    async def get_me(self, user_id: UUID) -> MeView:
        user = await self._users.get(user_id)
        profile = await self._profiles.get(user_id)
        if user is None or profile is None:
            raise UnauthorizedError()
        coach = (
            await self._coaches.get_active_for_user(user.id)
            if user.role is UserRole.coach
            else None
        )
        view = MeView(
            user=user,
            profile=profile,
            coach=coach,
            avatar_url=self._media.url(profile.avatar_key),
        )
        await self._session.commit()  # end the read transaction
        return view

    async def update_me(self, user_id: UUID, changes: Mapping[str, Any]) -> MeView:
        """``changes`` holds only the fields the client sent (``model_fields_set``)."""
        view = await self.get_me(user_id)
        if view.coach is None:
            forbidden = sorted(COACH_FIELDS & changes.keys())
            if forbidden:
                raise UnprocessableError(
                    "FIELD_NOT_ALLOWED",
                    "Field not allowed for this role",
                    details=[
                        {"field": name, "message": "only coaches can set this field"}
                        for name in forbidden
                    ],
                )
        for name, value in changes.items():
            if name in PROFILE_FIELDS:
                setattr(view.profile, name, value)
            elif view.coach is not None and name in COACH_FIELDS:
                setattr(view.coach, name, value)
        await self._session.commit()
        return await self.get_me(user_id)

    # --- avatar ----------------------------------------------------------------------------

    async def presign_avatar(
        self, *, content_type: str, content_length: int
    ) -> tuple[str, PresignedPut]:
        key = f"avatars/{new_id()}"
        presigned = await self._storage.presign_put(
            key, content_type, content_length, self._settings.presign_ttl_seconds
        )
        return key, presigned

    async def confirm_avatar(self, user_id: UUID, *, key: str) -> MeView:
        meta = await self._storage.head(key)
        if meta is None:
            raise NotFoundError()
        if meta.content_type not in AVATAR_CONTENT_TYPES or meta.size > AVATAR_MAX_BYTES:
            raise UnprocessableError(
                "INVALID_AVATAR", "Avatar must be a jpeg, png or webp image of at most 5 MB"
            )
        profile = await self._profiles.get(user_id)
        if profile is None:
            raise UnauthorizedError()
        profile.avatar_key = key
        await self._session.commit()
        return await self.get_me(user_id)

    # --- email change ----------------------------------------------------------------------

    async def request_email_change(self, user_id: UUID, *, new_email: str, password: str) -> None:
        user = await self._users.get(user_id)
        if user is None:
            raise UnauthorizedError()
        if not await self._hasher.verify(user.password_hash, password):
            raise ForbiddenError("INVALID_CURRENT_PASSWORD", "Current password is incorrect")
        # Early answer for the common case; ``uq_users_email`` still decides at confirm.
        if await self._users.get_by_email(new_email) is not None:
            raise EmailTakenError()
        raw = await self._tokens.issue(
            user_id=user.id,
            purpose=OttPurpose.email_change,
            ttl=_hours(self._settings.ott_ttl_hours[OttPurpose.email_change.value]),
            now=self._clock.now(),
            payload={"new_email": new_email},
        )
        await self._session.commit()
        await enqueue_best_effort(
            self._jobs,
            "send_email_change",
            user_id=str(user.id),
            token=raw,
            new_email=new_email,
        )

    async def confirm_email_change(self, *, token: str) -> None:
        """Consume, swap and revoke in one transaction; a conflict un-consumes the token."""
        now = self._clock.now()
        try:
            async with self._session.begin_nested():
                consumed = await self._tokens.consume(
                    raw=token, purpose=OttPurpose.email_change, now=now
                )
                if consumed is None:
                    raise token_invalid()
                user = await self._users.get(consumed.user_id)
                new_email = (consumed.payload or {}).get("new_email")
                if user is None or user.status is not UserStatus.active or not new_email:
                    raise token_invalid()  # the savepoint rolls the consume back
                user.email = new_email
                user.is_email_verified = True  # the link reached the new mailbox
                await self._session.flush()
        except IntegrityError as exc:
            if isinstance(map_integrity_error(exc), EmailTakenError):
                raise EmailTakenError() from exc
            raise
        await self._refresh_tokens.revoke_all_for_user(user.id, now=now)
        await self._audit.record(
            action="me.email_changed",
            actor_id=user.id,
            subject_type="user",
            subject_id=user.id,
        )
        await self._session.commit()


def _hours(hours: int) -> timedelta:
    return timedelta(hours=hours)
