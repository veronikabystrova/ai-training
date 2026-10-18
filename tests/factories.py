"""Pytest fixtures that build committed-in-session ORM rows (flushed, never committed)."""

from collections.abc import Awaitable, Callable
from datetime import date, datetime, time, timedelta
from itertools import count
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import PasswordHasher, TokenIssuer
from app.models.identity import (
    AvailabilityWindow,
    CoachProfile,
    LinkSource,
    PlayerProfile,
    Profile,
    ShareLink,
    ShareLinkKind,
    SubjectType,
    TrainerPlayerLink,
    TrainerProfile,
    User,
    UserRole,
    UserStatus,
)
from tests.fakes import FakeClock

_seq = count(1)
_HASHER = PasswordHasher(time_cost=1, memory_kib=8, parallelism=1)
_PASSWORD_HASH = _HASHER._h.hash("Correct-Horse-Battery-9")
TEST_PASSWORD = "Correct-Horse-Battery-9"
TEST_JWT_SECRET = "t" * 32

MakeUser = Callable[..., Awaitable[User]]
MakeTrainer = Callable[..., Awaitable[User]]
MakeCoach = Callable[..., Awaitable[CoachProfile]]
MakePlayer = Callable[..., Awaitable[PlayerProfile]]
MakeDependent = Callable[..., Awaitable[PlayerProfile]]
LinkPlayer = Callable[..., Awaitable[TrainerPlayerLink]]
AddWindow = Callable[..., Awaitable[AvailabilityWindow]]
AddShareLink = Callable[..., Awaitable[ShareLink]]
PrincipalToken = Callable[..., str]


def _parse(value: str | time) -> time:
    return time.fromisoformat(value) if isinstance(value, str) else value


@pytest.fixture
def make_user(db_session: AsyncSession) -> MakeUser:
    async def _make(
        *,
        email: str | None = None,
        role: UserRole = UserRole.player,
        status: UserStatus = UserStatus.active,
        first_name: str = "Test",
        last_name: str = "User",
    ) -> User:
        n = next(_seq)
        user = User(
            email=email or f"user{n}@example.com",
            password_hash=_PASSWORD_HASH,
            role=role,
            status=status,
            is_email_verified=True,
            profile=Profile(first_name=first_name, last_name=last_name),
        )
        db_session.add(user)
        await db_session.flush()
        return user

    return _make


@pytest.fixture
def make_trainer(db_session: AsyncSession, make_user: MakeUser) -> MakeUser:
    async def _make(**kwargs: object) -> User:
        user = await make_user(role=UserRole.trainer, **kwargs)
        n = next(_seq)
        db_session.add(
            TrainerProfile(user_id=user.id, business_name=f"Academy {n}", slug=f"academy-{n}")
        )
        await db_session.flush()
        return user

    return _make


@pytest.fixture
def make_coach(
    db_session: AsyncSession, make_user: MakeUser, clock: FakeClock
) -> Callable[..., Awaitable[CoachProfile]]:
    async def _make(trainer: User, *, user: User | None = None) -> CoachProfile:
        coach_user = user or await make_user(role=UserRole.coach)
        row = CoachProfile(user=coach_user, trainer_id=trainer.id, joined_at=clock.now())
        db_session.add(row)
        await db_session.flush()
        return row

    return _make


@pytest.fixture
def make_player(
    db_session: AsyncSession, make_user: MakeUser
) -> Callable[..., Awaitable[PlayerProfile]]:
    async def _make(*, owner: User | None = None, display_name: str | None = None) -> PlayerProfile:
        owner = owner or await make_user(role=UserRole.player)
        row = PlayerProfile(
            owner_user_id=owner.id,
            display_name=display_name or f"Player {next(_seq)}",
            birth_date=date(2012, 5, 1),
            gender="undisclosed",
        )
        db_session.add(row)
        await db_session.flush()
        return row

    return _make


@pytest.fixture
def make_dependent(
    db_session: AsyncSession, make_user: MakeUser
) -> Callable[..., Awaitable[PlayerProfile]]:
    """A child profile owned by a parent user, optionally with its own login user."""

    async def _make(
        *, parent: User | None = None, with_login: bool = False, birth_date: date = date(2015, 3, 1)
    ) -> PlayerProfile:
        parent = parent or await make_user(role=UserRole.player)
        login = await make_user(role=UserRole.player) if with_login else None
        row = PlayerProfile(
            owner_user_id=parent.id,
            login_user_id=login.id if login else None,
            display_name=f"Child {next(_seq)}",
            birth_date=birth_date,
            gender="undisclosed",
            is_dependent=True,
        )
        db_session.add(row)
        await db_session.flush()
        return row

    return _make


@pytest.fixture
def link_player(
    db_session: AsyncSession,
) -> Callable[..., Awaitable[TrainerPlayerLink]]:
    async def _link(trainer: User, player: PlayerProfile) -> TrainerPlayerLink:
        row = TrainerPlayerLink(
            trainer_id=trainer.id, player_profile_id=player.id, source=LinkSource.parent_added
        )
        db_session.add(row)
        await db_session.flush()
        return row

    return _link


@pytest.fixture
def add_window(db_session: AsyncSession) -> Callable[..., Awaitable[AvailabilityWindow]]:
    async def _add(
        subject_id: UUID,
        *,
        weekday: int,
        start: str | time,
        end: str | time,
        subject_type: SubjectType = "player",
    ) -> AvailabilityWindow:
        row = AvailabilityWindow(
            subject_type=subject_type,
            subject_id=subject_id,
            weekday=weekday,
            start_time=_parse(start),
            end_time=_parse(end),
        )
        db_session.add(row)
        await db_session.flush()
        return row

    return _add


@pytest.fixture
def add_share_link(
    db_session: AsyncSession, clock: FakeClock
) -> Callable[..., Awaitable[ShareLink]]:
    async def _add(
        trainer: User,
        *,
        kind: str | ShareLinkKind = ShareLinkKind.player_static,
        target_email: str | None = None,
        max_uses: int | None = None,
        expires_at: datetime | None = None,
    ) -> ShareLink:
        kind = ShareLinkKind(kind)
        if kind is ShareLinkKind.coach_unique:
            max_uses = 1 if max_uses is None else max_uses
            expires_at = expires_at or clock.now() + timedelta(days=7)
        row = ShareLink(
            code=f"code-{next(_seq)}",
            trainer_id=trainer.id,
            created_by_user_id=trainer.id,
            kind=kind,
            target_email=target_email,
            max_uses=max_uses,
            expires_at=expires_at,
        )
        db_session.add(row)
        await db_session.flush()
        return row

    return _add


@pytest.fixture
def principal_token(clock: FakeClock) -> Callable[..., str]:
    issuer = TokenIssuer(secret=TEST_JWT_SECRET, access_ttl=900, clock=clock)

    def _token(
        user: User, *, act: User | None = None, trainer_id: UUID | None = None, child: bool = False
    ) -> str:
        token, _ = issuer.issue_access(
            user_id=user.id,
            role=user.role.value,
            trainer_id=trainer_id or (user.id if user.role is UserRole.trainer else None),
            act=act.id if act else None,
            child=child,
        )
        return token

    return _token
