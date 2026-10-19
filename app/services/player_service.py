"""A parent's dependents, their child logins and trainer links, and the caller's contexts.

The service owns the transaction (repositories never commit). Reads end without a pending
transaction; every write commits once. Soft removal keeps history: profile, links and the
child login become inactive, nothing is deleted.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import Clock
from app.core.config import Settings
from app.core.errors import (
    ConflictError,
    EmailTakenError,
    ForbiddenError,
    NotFoundError,
    UnauthorizedError,
    UnprocessableError,
    map_integrity_error,
)
from app.core.media import MediaUrlBuilder
from app.core.security import PasswordHasher, PasswordPolicyPort
from app.models.identity import (
    Gender,
    LinkSource,
    LinkStatus,
    PlayerProfile,
    Profile,
    User,
    UserRole,
    UserStatus,
)
from app.repositories.player import PlayerProfileRepository, PlayerRow, ProfileTrainerLink
from app.repositories.profile import ProfileRepository
from app.repositories.refresh_token import RefreshTokenRepository
from app.repositories.trainer_player_link import TrainerPlayerLinkRepository
from app.repositories.user import UserRepository
from app.services.ports import CommitmentChecker

MIN_DEPENDENT_AGE = 1
MAX_DEPENDENT_AGE = 17  # exclusive upper bound is 18 (spec decision 2026-10-18)

_PROFILE_FIELDS = (
    "display_name",
    "birth_date",
    "gender",
    "skill_level",
    "token_spend_requires_approval",
)


def age_on(birth_date: date, today: date) -> int:
    return (
        today.year
        - birth_date.year
        - ((today.month, today.day) < (birth_date.month, birth_date.day))
    )


def validate_dependent_age(birth_date: date, today: date) -> None:
    """Dependents are 1 <= age < 18 on ``today`` (a future birth date is rejected too)."""
    if not MIN_DEPENDENT_AGE <= age_on(birth_date, today) <= MAX_DEPENDENT_AGE:
        raise UnprocessableError(
            "INVALID_AGE",
            "A dependent must be at least 1 and under 18 years old",
            details=[{"field": "birth_date", "message": "age must be between 1 and 17"}],
        )


@dataclass(frozen=True, slots=True)
class LoginInput:
    email: str
    password: str


@dataclass(frozen=True, slots=True)
class NewPlayer:
    display_name: str
    birth_date: date
    gender: Gender
    skill_level: str | None
    trainer_ids: Sequence[UUID]
    token_spend_requires_approval: bool
    login: LoginInput | None


@dataclass(frozen=True, slots=True)
class TrainerLinkView:
    trainer_id: UUID
    business_name: str
    status: LinkStatus
    source: LinkSource
    linked_at: datetime


@dataclass(frozen=True, slots=True)
class PlayerView:
    profile: PlayerProfile
    has_login: bool
    trainers: list[TrainerLinkView]


@dataclass(frozen=True, slots=True)
class ContextView:
    trainer_id: UUID
    business_name: str
    logo_url: str | None
    player_profile_ids: list[UUID]
    linked_at: datetime


def _link_view(link: ProfileTrainerLink) -> TrainerLinkView:
    return TrainerLinkView(
        trainer_id=link.trainer_id,
        business_name=link.business_name,
        status=link.status,
        source=link.source,
        linked_at=link.linked_at,
    )


class PlayerService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        settings: Settings,
        clock: Clock,
        hasher: PasswordHasher,
        policy: PasswordPolicyPort,
        commitments: CommitmentChecker,
    ) -> None:
        self._session = session
        self._clock = clock
        self._hasher = hasher
        self._policy = policy
        self._commitments = commitments
        self._media = MediaUrlBuilder(settings.media_base_url)
        self._players = PlayerProfileRepository(session)
        self._links = TrainerPlayerLinkRepository(session)
        self._users = UserRepository(session)
        self._profiles = ProfileRepository(session)
        self._refresh_tokens = RefreshTokenRepository(session)

    # --- reads -----------------------------------------------------------------------------

    async def list_players(self, *, user_id: UUID, child: bool) -> list[PlayerView]:
        if child:
            rows = await self._players.list_views_for_login(login_user_id=user_id)
        else:
            rows = await self._players.list_views_for_owner(owner_user_id=user_id)
        views = await self._assemble(rows)
        await self._session.commit()  # end the read transaction
        return views

    async def get_player(self, *, user_id: UUID, child: bool, player_id: UUID) -> PlayerView:
        if child:
            rows = await self._players.list_views_for_login(
                login_user_id=user_id, player_profile_id=player_id
            )
        else:
            rows = await self._players.list_views_for_owner(
                owner_user_id=user_id, player_profile_id=player_id
            )
        if not rows:
            raise NotFoundError()
        (view,) = await self._assemble(rows)
        await self._session.commit()
        return view

    async def contexts(self, *, user_id: UUID) -> list[ContextView]:
        """Valid ``?trainer_id`` values: active links of the caller's profiles, active trainers."""
        links = [
            link
            for link in await self._players.list_trainer_links(owner_user_id=user_id)
            if link.status is LinkStatus.active and link.trainer_active
        ]
        await self._session.commit()
        by_trainer: dict[UUID, list[ProfileTrainerLink]] = {}
        for link in links:
            by_trainer.setdefault(link.trainer_id, []).append(link)
        return [
            ContextView(
                trainer_id=trainer_id,
                business_name=group[0].business_name,
                logo_url=self._media.url(group[0].logo_key),
                player_profile_ids=list(dict.fromkeys(g.player_profile_id for g in group)),
                linked_at=min(g.linked_at for g in group),
            )
            for trainer_id, group in by_trainer.items()
        ]

    # --- dependents ------------------------------------------------------------------------

    async def create_player(
        self, *, owner_user_id: UUID, data: NewPlayer, allow_duplicate: bool
    ) -> PlayerView:
        validate_dependent_age(data.birth_date, self._clock.today())
        trainer_ids = list(dict.fromkeys(data.trainer_ids))
        linked = await self._links.active_trainer_ids_for_owner(
            trainer_ids=trainer_ids, owner_user_id=owner_user_id
        )
        if linked != set(trainer_ids):  # unknown, inactive and unrelated look the same: 404
            raise NotFoundError()
        if not allow_duplicate:
            duplicate = await self._players.find_duplicate(
                owner_user_id=owner_user_id,
                display_name=data.display_name,
                birth_date=data.birth_date,
            )
            if duplicate is not None:
                raise ConflictError(
                    "DUPLICATE_PLAYER", "A dependent with this name and birth date exists"
                )

        owner_profile = await self._profiles.get(owner_user_id)
        if owner_profile is None:
            raise UnauthorizedError()
        # Policy and hashing precede the first write, so no row lock is held while argon2 runs.
        # (The earlier reads have begun a transaction, so a pooled connection is held meanwhile.)
        login_hash = await self._hash_login(data.login, data.display_name)

        login_user_id: UUID | None = None
        if data.login is not None and login_hash is not None:
            login_user = await self._insert_child_user(
                data.login.email, login_hash, data.display_name, owner_profile.last_name
            )
            login_user_id = login_user.id
        player = await self._players.add(
            PlayerProfile(
                owner_user_id=owner_user_id,
                login_user_id=login_user_id,
                display_name=data.display_name,
                birth_date=data.birth_date,
                gender=data.gender,
                skill_level=data.skill_level,
                is_dependent=True,
                token_spend_requires_approval=data.token_spend_requires_approval,
            )
        )
        for trainer_id in trainer_ids:
            await self._links.upsert_active(
                trainer_id=trainer_id,
                player_profile_id=player.id,
                source=LinkSource.parent_added,
                share_link_id=None,
            )
        await self._session.commit()
        return await self.get_player(user_id=owner_user_id, child=False, player_id=player.id)

    async def update_player(
        self, *, owner_user_id: UUID, player_id: UUID, changes: Mapping[str, Any]
    ) -> PlayerView:
        """``changes`` holds only the fields the client sent (``model_fields_set``).

        ``login``: absent leaves the child login alone, ``None`` revokes it, a ``LoginInput``
        creates or replaces it.
        """
        player = await self._players.get_owned(owner_user_id=owner_user_id, id=player_id)
        if player is None:
            raise NotFoundError()
        if "login" in changes and not player.is_dependent:
            raise UnprocessableError(
                details=[{"field": "login", "message": "only a dependent can have a child login"}]
            )
        if player.is_dependent and "birth_date" in changes:
            validate_dependent_age(changes["birth_date"], self._clock.today())

        replacement: LoginInput | None = changes.get("login")
        login_hash = (
            await self._hash_login(replacement, changes.get("display_name") or player.display_name)
            if replacement is not None
            else None
        )

        for name in _PROFILE_FIELDS:
            if name in changes:
                setattr(player, name, changes[name])
        if "login" in changes:
            if replacement is None:
                await self._revoke_login(player)
            elif login_hash is not None:
                await self._set_login(player, replacement.email, login_hash)
        await self._session.commit()
        return await self.get_player(user_id=owner_user_id, child=False, player_id=player_id)

    async def remove_player(self, *, owner_user_id: UUID, player_id: UUID) -> None:
        player = await self._players.get_owned(owner_user_id=owner_user_id, id=player_id)
        if player is None:
            raise NotFoundError()
        if not player.is_dependent:
            raise ConflictError("CANNOT_REMOVE_OWN_PROFILE", "The own profile cannot be removed")
        active = await self._active_links(owner_user_id, player.id)
        await self._require_no_commitments(player.id, [link.trainer_id for link in active])
        for link in active:
            await self._links.deactivate(trainer_id=link.trainer_id, player_profile_id=player.id)
        player.is_active = False
        await self._revoke_login(player)
        await self._session.commit()

    # --- trainer links ---------------------------------------------------------------------

    async def add_trainer(
        self, *, owner_user_id: UUID, player_id: UUID, trainer_id: UUID
    ) -> tuple[TrainerLinkView, bool]:
        """Link a dependent to a trainer the parent already works with. True when changed."""
        player = await self._players.get_owned(owner_user_id=owner_user_id, id=player_id)
        if player is None:
            raise NotFoundError()
        await self._require_trainer_of_owner(trainer_id, owner_user_id)
        changed = await self._links.activate(
            trainer_id=trainer_id,
            player_profile_id=player.id,
            source=LinkSource.parent_added,
            share_link_id=None,
        )
        await self._session.commit()
        links = await self._players.list_trainer_links(
            owner_user_id=owner_user_id, player_profile_ids=[player.id]
        )
        link = next(link for link in links if link.trainer_id == trainer_id)
        await self._session.commit()
        return _link_view(link), changed

    async def remove_trainer(
        self, *, owner_user_id: UUID, player_id: UUID, trainer_id: UUID
    ) -> None:
        player = await self._players.get_owned(owner_user_id=owner_user_id, id=player_id)
        if player is None:
            raise NotFoundError()
        if not await self._links.has_active_link(
            trainer_id=trainer_id, player_profile_id=player.id
        ):
            raise NotFoundError()
        await self._require_no_commitments(player.id, [trainer_id])
        await self._links.deactivate(trainer_id=trainer_id, player_profile_id=player.id)
        await self._session.commit()

    # --- internals -------------------------------------------------------------------------

    async def _assemble(self, rows: list[PlayerRow]) -> list[PlayerView]:
        if not rows:
            return []
        links_by_profile: dict[UUID, list[TrainerLinkView]] = {}
        for owner_id in {r.profile.owner_user_id for r in rows}:
            ids = [r.profile.id for r in rows if r.profile.owner_user_id == owner_id]
            for link in await self._players.list_trainer_links(
                owner_user_id=owner_id, player_profile_ids=ids
            ):
                links_by_profile.setdefault(link.player_profile_id, []).append(_link_view(link))
        return [
            PlayerView(
                profile=r.profile,
                has_login=r.has_login,
                trainers=links_by_profile.get(r.profile.id, []),
            )
            for r in rows
        ]

    async def _require_trainer_of_owner(self, trainer_id: UUID, owner_user_id: UUID) -> None:
        # Unknown, inactive and unrelated trainers are indistinguishable: 404.
        if not await self._links.has_active_link_for_owner(
            trainer_id=trainer_id, owner_user_id=owner_user_id
        ):
            raise NotFoundError()

    async def _active_links(self, owner_user_id: UUID, player_id: UUID) -> list[ProfileTrainerLink]:
        links = await self._players.list_trainer_links(
            owner_user_id=owner_user_id, player_profile_ids=[player_id]
        )
        return [link for link in links if link.status is LinkStatus.active]

    async def _require_no_commitments(self, player_id: UUID, trainer_ids: list[UUID]) -> None:
        rsvp_ids: list[str] = []
        for trainer_id in trainer_ids:
            rsvp_ids += [
                str(i) for i in await self._commitments.future_paid_rsvps(player_id, trainer_id)
            ]
        if rsvp_ids:
            raise ConflictError(
                "ACTIVE_COMMITMENTS",
                "Future paid commitments block this change",
                details=[{"rsvp_ids": rsvp_ids}],
            )

    async def _hash_login(self, login: LoginInput | None, display_name: str) -> str | None:
        if login is None:
            return None
        self._policy.validate(login.password, [login.email, display_name])
        return await self._hasher.hash(login.password)

    async def _insert_child_user(
        self, email: str, password_hash: str, first_name: str, last_name: str
    ) -> User:
        user = User(
            email=email,
            password_hash=password_hash,
            role=UserRole.player,
            status=UserStatus.active,
            is_email_verified=True,  # the parent vouches for the address
            profile=Profile(first_name=first_name, last_name=last_name),
        )
        try:
            async with self._session.begin_nested():
                await self._users.add(user)
        except IntegrityError as exc:
            if isinstance(map_integrity_error(exc), EmailTakenError):
                raise EmailTakenError() from exc
            raise
        return user

    async def _set_login(self, player: PlayerProfile, email: str, password_hash: str) -> None:
        """Create the child login, or replace the credentials of the existing one."""
        if player.login_user_id is None:
            parent_profile = await self._profiles.get(player.owner_user_id)
            if parent_profile is None:
                raise UnauthorizedError()
            user = await self._insert_child_user(
                email, password_hash, player.display_name, parent_profile.last_name
            )
            player.login_user_id = user.id
            return
        existing = await self._users.get(player.login_user_id)
        if existing is None:
            raise ForbiddenError()
        user = existing
        try:
            async with self._session.begin_nested():
                user.email = email
                user.password_hash = password_hash
                user.status = UserStatus.active
                user.is_email_verified = True
                await self._session.flush()
        except IntegrityError as exc:
            if isinstance(map_integrity_error(exc), EmailTakenError):
                raise EmailTakenError() from exc
            raise
        await self._refresh_tokens.revoke_all_for_user(user.id, now=self._clock.now())

    async def _revoke_login(self, player: PlayerProfile) -> None:
        """Child login inactive and signed out; ``login_user_id`` stays for history."""
        if player.login_user_id is None:
            return
        await self._users.set_status(player.login_user_id, UserStatus.inactive)
        await self._refresh_tokens.revoke_all_for_user(player.login_user_id, now=self._clock.now())
