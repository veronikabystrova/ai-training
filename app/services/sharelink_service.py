"""ShareLink redemption: ``POST /auth/register`` (design "coach_unique redemption" flow).

One transaction per redemption. The link row is locked ``FOR UPDATE`` first, so concurrent
redemptions of one link serialise and the use counter cannot overshoot. Anything that fails
after a write simply raises: the request session is closed, and rolled back, by
``get_session``. Jobs are enqueued after the commit.
"""

from dataclasses import dataclass
from datetime import date
from typing import NoReturn
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import Clock
from app.core.config import Settings
from app.core.errors import (
    CoachAlreadyAssignedError,
    ConflictError,
    DomainError,
    EmailTakenError,
    ForbiddenError,
    NotFoundError,
    UnprocessableError,
    map_integrity_error,
)
from app.core.jobs import JobQueue, enqueue_best_effort
from app.core.security import ClientFingerprint, PasswordHasher, PasswordPolicyPort
from app.models.identity import (
    DISPLAY_NAME_MAX,
    CoachProfile,
    Gender,
    LinkSource,
    OttPurpose,
    PlayerProfile,
    Profile,
    ShareLink,
    ShareLinkKind,
    User,
    UserRole,
    UserStatus,
)
from app.repositories.coach_profile import CoachProfileRepository
from app.repositories.one_time_token import OneTimeTokenRepository
from app.repositories.player import PlayerProfileRepository
from app.repositories.share_link import ShareLinkRepository
from app.repositories.share_link_event import ShareLinkEventRepository
from app.repositories.trainer_player_link import TrainerPlayerLinkRepository
from app.repositories.user import UserRepository
from app.services.auth_service import AuthService, IssuedSession


@dataclass(frozen=True, slots=True)
class RegisterInput:
    # Identity fields are required only for a new account (see ``_require_identity``).
    email: str | None
    password: str | None
    first_name: str | None
    last_name: str | None
    phone: str | None
    timezone: str
    birth_date: date | None
    player_profile_ids: list[UUID] | None


@dataclass(frozen=True, slots=True)
class _Identity:
    """The identity fields of a new account, all present."""

    email: str
    password: str
    first_name: str
    last_name: str


@dataclass(frozen=True, slots=True)
class Registered:
    """A new account (201)."""

    issued: IssuedSession


@dataclass(frozen=True, slots=True)
class Linked:
    """An authenticated existing user linked to the trainer (200)."""

    user: User
    trainer_id: UUID | None  # the user's tenant after the redemption (coach move)
    child: bool
    linked_trainer_id: UUID


RegisterResult = Registered | Linked


def _birth_date_invalid(message: str) -> UnprocessableError:
    return UnprocessableError(
        details=[{"field": "birth_date", "message": message}],
    )


def _share_link_invalid() -> NotFoundError:
    return NotFoundError("SHARE_LINK_INVALID", "Share link is invalid or expired")


def _wrong_link_kind() -> DomainError:
    return ConflictError("WRONG_LINK_KIND", "This link cannot be redeemed by this account")


def _account_exists() -> DomainError:
    return ConflictError(
        "ACCOUNT_EXISTS", "An account with this email exists", [{"next": "login_then_redeem"}]
    )


class ShareLinkService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        settings: Settings,
        clock: Clock,
        hasher: PasswordHasher,
        policy: PasswordPolicyPort,
        jobs: JobQueue,
        auth: AuthService,
    ) -> None:
        self._session = session
        self._settings = settings
        self._clock = clock
        self._hasher = hasher
        self._policy = policy
        self._jobs = jobs
        self._auth = auth
        self._users = UserRepository(session)
        self._links = ShareLinkRepository(session)
        self._events = ShareLinkEventRepository(session)
        self._coaches = CoachProfileRepository(session)
        self._players = PlayerProfileRepository(session)
        self._tpl = TrainerPlayerLinkRepository(session)
        self._tokens = OneTimeTokenRepository(session)

    async def register_with_camp_submission(self) -> NoReturn:
        """Camp-submission conversion (FR-207) is stubbed until Epic-08 defines the mapping."""
        raise NotFoundError()

    async def redeem(
        self,
        *,
        code: str,
        data: RegisterInput,
        client: ClientFingerprint,
        caller_id: UUID | None,
        caller_is_child: bool,
    ) -> RegisterResult:
        """Redeem ``code`` for a new account (no caller) or for the authenticated caller."""
        if caller_id is None:
            identity = self._require_identity(data)
            # Policy and hashing precede the row lock: argon2 must not run under it.
            self._policy.validate(
                identity.password, [identity.email, identity.first_name, identity.last_name]
            )
            digest = await self._hasher.hash(identity.password)
            link = await self._lock_valid_link(code)
            return await self._register_new(link, data, identity, client, digest)

        link = await self._lock_valid_link(code)
        caller = await self._users.get(caller_id)
        if caller is None:
            raise NotFoundError()
        return await self._redeem_authenticated(
            link, data, client, caller=caller, caller_is_child=caller_is_child
        )

    @staticmethod
    def _require_identity(data: RegisterInput) -> _Identity:
        """A new account needs email, password and both names (422 per missing field)."""
        if (
            data.email is None
            or data.password is None
            or data.first_name is None
            or data.last_name is None
        ):
            fields = (
                ("email", data.email),
                ("password", data.password),
                ("first_name", data.first_name),
                ("last_name", data.last_name),
            )
            raise UnprocessableError(
                details=[
                    {"field": name, "message": "required to create an account"}
                    for name, value in fields
                    if value is None
                ]
            )
        return _Identity(data.email, data.password, data.first_name, data.last_name)

    # --- link validation -------------------------------------------------------------------

    async def _lock_valid_link(self, code: str) -> ShareLink:
        link = await self._links.lock_by_code(code)
        now = self._clock.now()
        if (
            link is None
            or not link.is_active
            or (link.expires_at is not None and link.expires_at <= now)
            or (link.max_uses is not None and link.use_count >= link.max_uses)
        ):
            raise _share_link_invalid()
        trainer = await self._users.get(link.trainer_id)
        if trainer is None or trainer.status is not UserStatus.active:
            raise _share_link_invalid()  # G-15: a deactivated tenant is invisible
        return link

    async def _consume_use(self, link: ShareLink, client: ClientFingerprint) -> None:
        if not await self._links.increment_use(link.id, trainer_id=link.trainer_id):
            raise _share_link_invalid()
        await self._events.append(
            share_link_id=link.id,
            kind="registered",
            user_agent_hash=client.user_agent_hash,
            ip_hash=client.ip_hash,
        )

    @staticmethod
    def _check_target_email(link: ShareLink, email: str) -> None:
        if link.target_email is None or link.target_email.lower() != email.lower():
            raise ForbiddenError("LINK_EMAIL_MISMATCH", "This link was issued to another email")

    # --- new account -----------------------------------------------------------------------

    async def _register_new(
        self,
        link: ShareLink,
        data: RegisterInput,
        identity: _Identity,
        client: ClientFingerprint,
        digest: str,
    ) -> Registered:
        is_coach = link.kind is ShareLinkKind.coach_unique
        if is_coach:
            self._check_target_email(link, identity.email)
        if data.player_profile_ids is not None:
            raise NotFoundError()  # a new account owns no profiles
        birth_date = None if is_coach else self._required_birth_date(data)

        user = User(
            email=identity.email,
            password_hash=digest,
            role=UserRole.coach if is_coach else UserRole.player,
            status=UserStatus.active,
            is_email_verified=False,
            profile=Profile(
                first_name=identity.first_name,
                last_name=identity.last_name,
                phone=data.phone,
                timezone=data.timezone,
            ),
        )
        await self._insert_user(user, is_coach=is_coach)

        if is_coach:
            await self._coaches.add(
                CoachProfile(
                    user_id=user.id, trainer_id=link.trainer_id, joined_at=self._clock.now()
                )
            )
        else:
            profile = await self._players.add(
                PlayerProfile(
                    owner_user_id=user.id,
                    display_name=f"{identity.first_name} {identity.last_name}"[:DISPLAY_NAME_MAX],
                    birth_date=birth_date,
                    gender=Gender.undisclosed,
                )
            )
            await self._tpl.upsert_active(
                trainer_id=link.trainer_id,
                player_profile_id=profile.id,
                source=LinkSource.share_link,
                share_link_id=link.id,
            )

        await self._consume_use(link, client)
        verify_token = await self._tokens.issue(
            user_id=user.id,
            purpose=OttPurpose.email_verify,
            ttl=self._auth.ott_ttl(OttPurpose.email_verify),
            now=self._clock.now(),
        )
        issued = await self._auth.issue_session(user, client)
        await self._session.commit()

        await enqueue_best_effort(self._jobs, "send_welcome_email", user_id=str(user.id))
        await enqueue_best_effort(
            self._jobs, "send_verification_email", user_id=str(user.id), token=verify_token
        )
        return Registered(issued)

    def _required_birth_date(self, data: RegisterInput) -> date:
        """A new player account must state a birth date, never in the future (422)."""
        if data.birth_date is None:
            raise _birth_date_invalid("required to create a player account")
        if data.birth_date > self._clock.now().date():
            raise _birth_date_invalid("must not be in the future")
        return data.birth_date

    async def _insert_user(self, user: User, *, is_coach: bool) -> None:
        """Insert in a savepoint; a duplicate email becomes the flow's conflict, not a 500."""
        try:
            async with self._session.begin_nested():
                await self._users.add(user)
        except IntegrityError as exc:
            if isinstance(map_integrity_error(exc), EmailTakenError):
                if is_coach:
                    raise _wrong_link_kind() from exc
                raise _account_exists() from exc
            raise

    # --- authenticated existing user -------------------------------------------------------

    async def _redeem_authenticated(
        self,
        link: ShareLink,
        data: RegisterInput,
        client: ClientFingerprint,
        *,
        caller: User,
        caller_is_child: bool,
    ) -> Linked:
        if caller_is_child:
            await self._notify_parent_blocked(caller, link)
            raise ForbiddenError("CHILD_CANNOT_REDEEM", "A child account cannot redeem a link")
        if not caller.is_email_verified:
            raise ForbiddenError("EMAIL_NOT_VERIFIED", "Verify your email first")

        if link.kind is ShareLinkKind.coach_unique:
            if caller.role is not UserRole.coach:
                raise _wrong_link_kind()
            self._check_target_email(link, caller.email)
            await self._move_coach(caller, link)
            changed = True
        else:
            if caller.role is not UserRole.player:
                raise _wrong_link_kind()
            changed = await self._link_profiles(caller, link, data.player_profile_ids)

        if changed:  # an already active link is a no-op: no use, no event (decision 2026-10-18)
            await self._consume_use(link, client)
        trainer_id = await self._auth.tenant_of(caller)
        child = await self._auth.is_child(caller)
        await self._session.commit()
        return Linked(
            user=caller, trainer_id=trainer_id, child=child, linked_trainer_id=link.trainer_id
        )

    async def _notify_parent_blocked(self, child: User, link: ShareLink) -> None:
        profile = await self._players.get_by_login_user(child.id)
        if profile is not None:
            await enqueue_best_effort(
                self._jobs,
                "notify_parent_share_link_blocked",
                player_profile_id=str(profile.id),
                share_link_id=str(link.id),
            )

    async def _link_profiles(
        self, caller: User, link: ShareLink, requested: list[UUID] | None
    ) -> bool:
        """Link the targets; True when at least one link was created or reactivated."""
        owned = await self._players.list_by_owner(caller.id)
        if requested is None:
            targets = [p for p in owned if not p.is_dependent]
        else:
            by_id = {p.id: p for p in owned}
            if any(pid not in by_id for pid in requested):
                raise NotFoundError()  # not owned: indistinguishable from unknown
            targets = [by_id[pid] for pid in dict.fromkeys(requested)]
        if not targets:
            raise NotFoundError()
        changed = False
        for profile in targets:
            changed |= await self._tpl.activate(
                trainer_id=link.trainer_id,
                player_profile_id=profile.id,
                source=LinkSource.share_link,
                share_link_id=link.id,
            )
        return changed

    async def _move_coach(self, caller: User, link: ShareLink) -> None:
        tenure = CoachProfile(
            user_id=caller.id, trainer_id=link.trainer_id, joined_at=self._clock.now()
        )
        try:
            async with self._session.begin_nested():
                await self._coaches.add(tenure)
        except IntegrityError as exc:
            if not isinstance(map_integrity_error(exc), CoachAlreadyAssignedError):
                raise
            current = await self._coaches.get_active_for_user(caller.id)
            details = [{"current_trainer_id": str(current.trainer_id)}] if current else []
            raise CoachAlreadyAssignedError(details=details) from exc
