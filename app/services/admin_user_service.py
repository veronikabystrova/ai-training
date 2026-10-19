"""Super-admin user management: directory, provisioning, lifecycle and GDPR erasure.

The service owns the transaction. Provisioning inserts inside a savepoint so a unique violation
leaves nothing behind; erasure locks the user row so two admins cannot erase it twice.
Jobs are enqueued after the commit.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import Clock
from app.core.config import Settings
from app.core.errors import ConflictError, NotFoundError, map_integrity_error
from app.core.jobs import JobQueue, enqueue_best_effort
from app.core.security import PasswordHasher, new_opaque_token
from app.models.identity import (
    OttPurpose,
    Profile,
    TrainerProfile,
    User,
    UserRole,
    UserStatus,
)
from app.repositories.coach_profile import CoachProfileRepository
from app.repositories.erasure import UserErasureRepository
from app.repositories.one_time_token import OneTimeTokenRepository
from app.repositories.profile import ProfileRepository, TrainerProfileRepository
from app.repositories.refresh_token import RefreshTokenRepository
from app.repositories.user import UserRepository
from app.services.audit_service import AuditService
from app.services.erasure import email_fingerprint, erased_email, retention_until

PROFILE_FIELDS = frozenset({"first_name", "last_name", "phone", "timezone"})


@dataclass(frozen=True, slots=True)
class AdminUserView:
    """A user with the joined data the admin schemas need."""

    user: User
    profile: Profile
    trainer_id: UUID | None  # trainer: self; coach: active tenure's trainer; else None
    business_name: str | None  # trainers only
    slug: str | None  # trainers only


@dataclass(frozen=True, slots=True)
class NewTrainer:
    email: str
    first_name: str
    last_name: str
    phone: str | None
    business_name: str
    slug: str
    timezone: str


def _user_erased() -> ConflictError:
    return ConflictError("USER_ERASED", "This user has been erased")


def _invalid_transition(current: UserStatus, target: UserStatus) -> ConflictError:
    return ConflictError(
        "INVALID_STATUS_TRANSITION",
        f"Cannot change status from {current.value} to {target.value}",
    )


class AdminUserService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        settings: Settings,
        clock: Clock,
        hasher: PasswordHasher,
        audit: AuditService,
        jobs: JobQueue,
    ) -> None:
        self._session = session
        self._settings = settings
        self._clock = clock
        self._hasher = hasher
        self._audit = audit
        self._jobs = jobs
        self._users = UserRepository(session)
        self._profiles = ProfileRepository(session)
        self._trainers = TrainerProfileRepository(session)
        self._coaches = CoachProfileRepository(session)
        self._tokens = OneTimeTokenRepository(session)
        self._refresh_tokens = RefreshTokenRepository(session)
        self._erasures = UserErasureRepository(session)

    # --- reads -----------------------------------------------------------------------------

    async def list_users(
        self,
        *,
        query: str | None,
        role: UserRole | None,
        status: UserStatus | None,
        trainer_id: UUID | None,
        cursor: str | None,
        limit: int,
    ) -> tuple[list[AdminUserView], str | None]:
        users, next_cursor = await self._users.list_directory(
            query=query, role=role, status=status, trainer_id=trainer_id, cursor=cursor, limit=limit
        )
        tenants = await self._coaches.active_trainer_ids(
            [u.id for u in users if u.role is UserRole.coach]
        )
        views = [
            AdminUserView(
                user=u,
                profile=u.profile,
                trainer_id=u.id if u.role is UserRole.trainer else tenants.get(u.id),
                business_name=None,
                slug=None,
            )
            for u in users
        ]
        await self._session.commit()  # end the read transaction
        return views, next_cursor

    async def get_user(self, user_id: UUID) -> AdminUserView:
        view = await self._view(user_id)
        await self._session.commit()  # end the read transaction
        return view

    async def _view(self, user_id: UUID) -> AdminUserView:
        user = await self._users.get(user_id)
        profile = await self._profiles.get(user_id)
        if user is None or profile is None:
            raise NotFoundError()
        trainer_id: UUID | None = None
        business_name: str | None = None
        slug: str | None = None
        if user.role is UserRole.trainer:
            trainer_id = user.id
            branding = await self._trainers.get(user.id)
            if branding is not None:
                business_name, slug = branding.business_name, branding.slug
        elif user.role is UserRole.coach:
            tenure = await self._coaches.get_active_for_user(user.id)
            trainer_id = tenure.trainer_id if tenure else None
        return AdminUserView(user, profile, trainer_id, business_name, slug)

    # --- provisioning ----------------------------------------------------------------------

    async def provision_trainer(self, *, admin_id: UUID, data: NewTrainer) -> AdminUserView:
        """Users, profiles and trainer_profiles in one transaction; the invite goes out after."""
        now = self._clock.now()
        # Nobody knows this password: the trainer sets one through the invite (password/setup).
        digest = await self._hasher.hash(new_opaque_token())
        user = User(
            email=data.email,
            password_hash=digest,
            role=UserRole.trainer,
            status=UserStatus.pending,
            is_email_verified=False,
            must_change_password=True,
            profile=Profile(
                first_name=data.first_name,
                last_name=data.last_name,
                phone=data.phone,
                timezone=data.timezone,
            ),
        )
        try:
            async with self._session.begin_nested():
                await self._users.add(user)
                await self._trainers.add(
                    TrainerProfile(
                        user_id=user.id, business_name=data.business_name, slug=data.slug
                    )
                )
        except IntegrityError as exc:
            mapped = map_integrity_error(exc)  # EMAIL_TAKEN / SLUG_TAKEN by constraint name
            if mapped is None:
                raise
            raise mapped from exc
        raw = await self._tokens.issue(
            user_id=user.id,
            purpose=OttPurpose.trainer_setup,
            ttl=timedelta(hours=self._settings.ott_ttl_hours[OttPurpose.trainer_setup.value]),
            now=now,
        )
        await self._audit.record(
            action="trainer.created",
            actor_id=admin_id,
            subject_type="user",
            subject_id=user.id,
            trainer_id=user.id,
        )
        view = await self._view(user.id)
        await self._session.commit()
        await enqueue_best_effort(
            self._jobs, "send_trainer_invite", user_id=str(user.id), token=raw
        )
        return view

    # --- edits and lifecycle ---------------------------------------------------------------

    async def update_user(
        self, *, admin_id: UUID, user_id: UUID, changes: Mapping[str, Any]
    ) -> AdminUserView:
        """``changes`` holds only the fields the admin sent. Role and email are immutable."""
        user = await self._locked(user_id)
        if user.status is UserStatus.deleted:
            raise _user_erased()
        profile = await self._profiles.get(user_id)
        if profile is None:
            raise NotFoundError()
        for name, value in changes.items():
            if name in PROFILE_FIELDS:
                setattr(profile, name, value)
        await self._audit.record(
            action="user.updated",
            actor_id=admin_id,
            subject_type="user",
            subject_id=user_id,
            metadata={"fields": sorted(changes)},  # names only, never values
        )
        view = await self._view(user_id)
        await self._session.commit()
        return view

    async def deactivate(self, *, admin_id: UUID, user_id: UUID) -> AdminUserView:
        """``active -> inactive``; every refresh family ends, so only the access token lives on
        until the next request, where the DB revalidation rejects it (A-03)."""
        user = await self._locked(user_id)
        if user.status is UserStatus.deleted:
            raise _user_erased()
        if user.status is UserStatus.pending:
            raise _invalid_transition(user.status, UserStatus.inactive)
        if user.status is UserStatus.active:
            user.status = UserStatus.inactive
            await self._refresh_tokens.revoke_all_for_user(user.id, now=self._clock.now())
            await self._audit.record(
                action="user.deactivated",
                actor_id=admin_id,
                subject_type="user",
                subject_id=user.id,
            )
        view = await self._view(user_id)
        await self._session.commit()
        return view

    async def reactivate(self, *, admin_id: UUID, user_id: UUID) -> AdminUserView:
        user = await self._locked(user_id)
        if user.status is UserStatus.deleted:
            raise _user_erased()
        if user.status is UserStatus.pending:
            raise _invalid_transition(user.status, UserStatus.active)  # only password/setup
        if user.status is UserStatus.inactive:
            user.status = UserStatus.active
            await self._audit.record(
                action="user.reactivated",
                actor_id=admin_id,
                subject_type="user",
                subject_id=user.id,
            )
        view = await self._view(user_id)
        await self._session.commit()
        return view

    async def _locked(self, user_id: UUID) -> User:
        user = await self._users.lock(user_id)
        if user is None:
            raise NotFoundError()
        return user

    # --- erasure ---------------------------------------------------------------------------

    async def erase(self, *, admin_id: UUID, user_id: UUID) -> None:
        """Anonymise in place (never delete), including the user's players and child logins."""
        user = await self._locked(user_id)
        if user.status is UserStatus.deleted:
            raise ConflictError("ALREADY_ERASED", "This user has already been erased")
        now = self._clock.now()
        erased_ids = [user.id]
        await self._erase_account(admin_id, user, now)

        owned = await self._erasures.scrub_owned_profiles(user.id)
        profile_ids = [profile_id for profile_id, _ in owned]
        await self._erasures.deactivate_links(profile_ids)
        # A coach's own availability is keyed by the user id; players' by their profile id.
        await self._erasures.delete_availability([user.id, *profile_ids])
        for _, login_user_id in owned:
            if login_user_id is None or login_user_id == user.id:
                continue
            child = await self._users.lock(login_user_id)
            if child is not None and child.status is not UserStatus.deleted:
                await self._erase_account(admin_id, child, now)
                erased_ids.append(child.id)

        await self._erasures.delete_one_time_tokens(erased_ids)
        await self._erasures.end_coach_tenures(user.id, now=now)
        if user.role is UserRole.trainer:
            await self._erasures.scrub_trainer_profile(user.id)
        await self._audit.record(
            action="user.erased",
            actor_id=admin_id,
            subject_type="user",
            subject_id=user.id,
        )
        await self._session.commit()

    async def _erase_account(self, admin_id: UUID, user: User, now: datetime) -> None:
        """Scrub one account: PII, credentials, sessions, share links, ledger row."""
        original_email = user.email
        placeholder = erased_email(user.id)
        await self._users.anonymise(user.id, email=placeholder, now=now)
        await self._refresh_tokens.revoke_all_for_user(user.id, now=now)
        await self._erasures.scrub_share_link_email(original_email, placeholder=placeholder)
        await self._erasures.append(
            original_user_id=user.id,
            original_email_hash=email_fingerprint(original_email),
            erased_by_user_id=admin_id,
            reason=None,
            legal_retention_until=retention_until(
                now, years=self._settings.erasure_retention_years
            ),
        )
