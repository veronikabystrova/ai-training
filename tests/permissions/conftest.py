"""Module-scoped world for the permission suite.

Seeding (users, tenants, links, tokens) runs once per test module inside one outer transaction
that is rolled back when the module ends. Each test runs inside a savepoint that is rolled back
after it, so mutating cells (DELETE, erase, ...) never leak into the next cell. Rate limits are
lifted so the ~400 cells never trip a 429.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from redis import Redis
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool
from testcontainers.redis import RedisContainer

from app.core.config import Settings
from app.core.ids import new_id
from app.core.ratelimit import RouteClass
from app.core.security import TokenIssuer
from app.main import create_app
from app.models.identity import (
    CoachProfile,
    Gender,
    LinkSource,
    PlayerProfile,
    Profile,
    PurchaseApproval,
    ShareLink,
    ShareLinkKind,
    TrainerPlayerLink,
    TrainerProfile,
    User,
    UserRole,
    UserStatus,
)
from app.repositories.impersonation import ImpersonationSessionRepository
from tests.factories import _PASSWORD_HASH, TEST_JWT_SECRET
from tests.fakes import (
    FakeClock,
    FakeCommitmentChecker,
    FakeJobQueue,
    FakeObjectStorage,
    FakePurchaseExecutor,
)

MODULE_LOOP = pytest.mark.asyncio(loop_scope="module")


@dataclass
class Tenant:
    """One trainer with a coach, a parent, the parent's player and an approval."""

    trainer: UUID
    slug: str
    coach: UUID
    parent: UUID
    player: UUID  # the parent's own player profile
    share_link: UUID
    share_code: str
    approval: UUID


@dataclass
class World:
    client: AsyncClient
    conn: AsyncConnection
    storage: FakeObjectStorage
    a: Tenant
    b: Tenant
    ids: dict[str, UUID]  # named ids shared by both tenants (child, targets, extra trainer)
    emails: dict[str, str]
    headers: dict[str, dict[str, str]]
    refresh: dict[str, str] = field(default_factory=dict)  # role -> refresh token


@pytest.fixture(scope="module")
def perm_redis_url(redis_container: RedisContainer) -> str:
    host = redis_container.get_container_host_ip()
    port = redis_container.get_exposed_port(6379)
    with Redis(host=host, port=int(port)) as client:
        client.flushall()
    return f"redis://{host}:{port}"


def _user(role: UserRole, email: str, *, status: UserStatus = UserStatus.active) -> User:
    return User(
        email=email,
        password_hash=_PASSWORD_HASH,
        role=role,
        status=status,
        is_email_verified=True,
        profile=Profile(first_name="Perm", last_name=role.value),
    )


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _tenant(
    s: AsyncSession, tag: str, now: datetime, ids: dict[str, UUID], emails: dict[str, str]
) -> Tenant:
    trainer = _user(UserRole.trainer, f"trainer-{tag}@example.com")
    coach = _user(UserRole.coach, f"coach-{tag}@example.com")
    parent = _user(UserRole.player, f"parent-{tag}@example.com")
    s.add_all([trainer, coach, parent])
    await s.flush()
    slug = f"perm-{tag}"
    s.add(TrainerProfile(user_id=trainer.id, business_name=f"Perm {tag}", slug=slug))
    await s.flush()
    s.add(CoachProfile(user_id=coach.id, trainer_id=trainer.id, joined_at=now))
    player = PlayerProfile(
        owner_user_id=parent.id,
        display_name=f"Player {tag}",
        birth_date=date(2012, 5, 1),
        gender=Gender.undisclosed,
    )
    s.add(player)
    await s.flush()
    s.add(
        TrainerPlayerLink(
            trainer_id=trainer.id, player_profile_id=player.id, source=LinkSource.parent_added
        )
    )
    link = ShareLink(
        code=f"perm-code-{tag}",
        trainer_id=trainer.id,
        created_by_user_id=trainer.id,
        kind=ShareLinkKind.player_static,
    )
    approval = PurchaseApproval(
        player_profile_id=player.id,
        parent_user_id=parent.id,
        trainer_id=trainer.id,
        subject_type="pack",
        subject_id=new_id(),
        amount_cents=1250,
        token_amount=None,
        expires_at=now + timedelta(hours=48),
    )
    s.add_all([link, approval])
    await s.flush()
    emails[f"trainer_{tag}"] = trainer.email
    emails[f"coach_{tag}"] = coach.email
    emails[f"parent_{tag}"] = parent.email
    return Tenant(
        trainer=trainer.id,
        slug=slug,
        coach=coach.id,
        parent=parent.id,
        player=player.id,
        share_link=link.id,
        share_code=link.code,
        approval=approval.id,
    )


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def world(database_url: str, perm_redis_url: str) -> AsyncIterator[World]:
    clock = FakeClock(datetime(2026, 10, 18, 10, 0, tzinfo=UTC))
    settings = Settings(
        database_url=database_url,
        redis_url=perm_redis_url,
        jwt_secret=TEST_JWT_SECRET,
        hash_salt="s" * 16,
        argon2_time_cost=1,
        argon2_memory_kib=8,
        argon2_parallelism=1,
        # Authorization, not throttling, must decide every cell.
        rate_limit_overrides={route.name: "100000/minute" for route in RouteClass},
    )
    engine = create_async_engine(database_url, poolclass=NullPool)
    storage = FakeObjectStorage()
    async with engine.connect() as conn:
        outer = await conn.begin()

        def new_session() -> AsyncSession:
            return AsyncSession(
                bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False
            )

        @asynccontextmanager
        async def session_factory() -> AsyncIterator[AsyncSession]:
            async with new_session() as session:
                yield session

        app: FastAPI = create_app(settings, clock=clock)
        app.state.session_factory = session_factory
        app.state.effects = []
        app.state.job_queue = FakeJobQueue()
        app.state.storage = storage
        app.state.commitment_checker = FakeCommitmentChecker()
        app.state.purchase_executor = FakePurchaseExecutor()

        ids: dict[str, UUID] = {}
        emails: dict[str, str] = {}
        issuer = TokenIssuer(secret=TEST_JWT_SECRET, access_ttl=900, clock=clock)
        async with new_session() as s:
            now = clock.now()
            a = await _tenant(s, "a", now, ids, emails)
            b = await _tenant(s, "b", now, ids, emails)
            extra_trainer = _user(UserRole.trainer, "trainer-c@example.com")
            admin = _user(UserRole.super_admin, "admin@example.com")
            child_login = _user(UserRole.player, "child-a@example.com")
            target = _user(UserRole.player, "target@example.com")
            inactive = _user(UserRole.player, "inactive@example.com", status=UserStatus.inactive)
            s.add_all([extra_trainer, admin, child_login, target, inactive])
            await s.flush()
            s.add(TrainerProfile(user_id=extra_trainer.id, business_name="Perm c", slug="perm-c"))
            child = PlayerProfile(
                owner_user_id=a.parent,
                login_user_id=child_login.id,
                display_name="Child a",
                birth_date=date(2015, 3, 1),
                gender=Gender.undisclosed,
                is_dependent=True,
            )
            s.add(child)
            await s.flush()
            s.add(
                TrainerPlayerLink(
                    trainer_id=a.trainer, player_profile_id=child.id, source=LinkSource.parent_added
                )
            )
            # The parent's own player is also linked to trainer c, so "add c to the child"
            # is an allowed move (a trainer the parent is already linked to).
            s.add(
                TrainerPlayerLink(
                    trainer_id=extra_trainer.id,
                    player_profile_id=a.player,
                    source=LinkSource.parent_added,
                )
            )
            await s.flush()
            ids.update(
                admin=admin.id,
                child=child.id,
                child_login=child_login.id,
                extra_trainer=extra_trainer.id,
                target=target.id,
                inactive=inactive.id,
            )
            emails["child"] = child_login.email
            emails["admin"] = admin.email

            def access(user_id: UUID, role: UserRole, **kw: object) -> str:
                token, _ = issuer.issue_access(
                    user_id=user_id,
                    role=role.value,
                    **kw,  # type: ignore[arg-type]
                )
                return token

            headers: dict[str, dict[str, str]] = {
                "anonymous": {},
                "admin": _bearer(
                    access(admin.id, UserRole.super_admin, trainer_id=None, act=None, child=False)
                ),
                "trainer": _bearer(
                    access(a.trainer, UserRole.trainer, trainer_id=a.trainer, act=None, child=False)
                ),
                "coach": _bearer(
                    access(a.coach, UserRole.coach, trainer_id=a.trainer, act=None, child=False)
                ),
                "parent": _bearer(
                    access(a.parent, UserRole.player, trainer_id=None, act=None, child=False)
                ),
                "child": _bearer(
                    access(child_login.id, UserRole.player, trainer_id=None, act=None, child=True)
                ),
            }
            repo = ImpersonationSessionRepository(s)
            for role, target_id, target_role, tid in (
                ("imp_trainer", a.trainer, UserRole.trainer, a.trainer),
                ("imp_parent", a.parent, UserRole.player, None),
            ):
                jti = new_id()
                await repo.open(
                    admin_user_id=admin.id,
                    target_user_id=target_id,
                    jti=jti,
                    expires_at=now + timedelta(minutes=60),
                    reason=None,
                )
                headers[role] = _bearer(
                    access(
                        target_id,
                        target_role,
                        trainer_id=tid,
                        act=admin.id,
                        child=False,
                        jti=jti,
                        ttl=7200,
                    )
                )
            await s.commit()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            refresh: dict[str, str] = {}
            for role, key in (
                ("admin", "admin"),
                ("trainer", "trainer_a"),
                ("coach", "coach_a"),
                ("parent", "parent_a"),
                ("child", "child"),
            ):
                r = await client.post(
                    "/api/v1/auth/login",
                    json={"email": emails[key], "password": "Correct-Horse-Battery-9"},
                )
                assert r.status_code == 200, r.text
                refresh[role] = r.json()["tokens"]["refresh_token"]
            refresh["imp_trainer"] = refresh["trainer"]
            refresh["imp_parent"] = refresh["parent"]
            yield World(
                client=client,
                conn=conn,
                storage=storage,
                a=a,
                b=b,
                ids=ids,
                emails=emails,
                headers=headers,
                refresh=refresh,
            )
        await outer.rollback()
    await engine.dispose()


@pytest_asyncio.fixture(loop_scope="module")
async def cell_world(world: World) -> AsyncIterator[World]:
    """The world for one request; whatever that request changed is rolled back afterwards."""
    nested = await world.conn.begin_nested()
    try:
        yield world
    finally:
        if nested.is_active:
            await nested.rollback()
        world.storage.objects.clear()
