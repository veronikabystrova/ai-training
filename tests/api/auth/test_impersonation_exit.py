from collections.abc import Awaitable, Callable
from datetime import timedelta

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.identity import UserRole
from app.repositories.refresh_token import RefreshTokenRepository
from tests.api.auth.helpers import REFRESH, code, login
from tests.api.helpers import bearer
from tests.factories import (
    ImpersonationToken,
    MakeTrainer,
    MakeUser,
    PrincipalToken,
)
from tests.fakes import FakeClock

EXIT = "/api/v1/auth/impersonation/exit"
ImpersonationFactory = Callable[..., Awaitable[ImpersonationToken]]


async def _family(db: AsyncSession, imp: ImpersonationToken, clock: FakeClock) -> str:
    """The session's refresh family, linked by ``family_id == session jti``."""
    return await RefreshTokenRepository(db).issue(
        user_id=imp.session.target_user_id,
        family_id=imp.jti,
        expires_at=clock.now() + timedelta(days=30),
    )


async def test_exit_ends_session_revokes_family_and_token(
    client: AsyncClient,
    db_session: AsyncSession,
    make_trainer: MakeTrainer,
    impersonation_token: ImpersonationFactory,
    clock: FakeClock,
) -> None:
    imp = await impersonation_token(await make_trainer())
    refresh = await _family(db_session, imp, clock)
    headers = bearer(imp.access)
    assert (await client.get("/probe/principal", headers=headers)).status_code == 200

    r = await client.post(EXIT, headers=headers)
    assert r.status_code == 204
    assert r.content == b""
    await db_session.refresh(imp.session)
    assert imp.session.ended_at == clock.now()

    after = await client.get("/probe/principal", headers=headers)
    assert after.status_code == 401
    assert code(after) == "IMPERSONATION_EXPIRED"
    again = await client.post(EXIT, headers=headers)
    assert (again.status_code, code(again)) == (401, "IMPERSONATION_EXPIRED")
    gone = await client.post(REFRESH, json={"refresh_token": refresh})
    assert (gone.status_code, code(gone)) == (401, "INVALID_REFRESH_TOKEN")


async def test_exit_leaves_the_targets_own_sessions_and_other_impersonations_alone(
    client: AsyncClient,
    make_trainer: MakeTrainer,
    make_user: MakeUser,
    impersonation_token: ImpersonationFactory,
) -> None:
    target = await make_trainer(email="t@x.com")
    own = (await login(client, "t@x.com"))["tokens"]["refresh_token"]
    first = await impersonation_token(target)
    second = await impersonation_token(target)

    assert (await client.post(EXIT, headers=bearer(first.access))).status_code == 204
    assert (await client.post(REFRESH, json={"refresh_token": own})).status_code == 200
    assert (await client.get("/probe/principal", headers=bearer(second.access))).status_code == 200


async def test_exit_by_a_non_impersonating_principal_is_403(
    client: AsyncClient, make_user: MakeUser, principal_token: PrincipalToken
) -> None:
    user = await make_user(role=UserRole.player)
    r = await client.post(EXIT, headers=bearer(principal_token(user)))
    assert r.status_code == 403
    assert code(r) == "FORBIDDEN"
    admin = await make_user(role=UserRole.super_admin)
    r = await client.post(EXIT, headers=bearer(principal_token(admin)))
    assert (r.status_code, code(r)) == (403, "FORBIDDEN")


async def test_exit_requires_authentication(client: AsyncClient) -> None:
    r = await client.post(EXIT)
    assert (r.status_code, code(r)) == (401, "INVALID_TOKEN")


async def test_exit_after_session_expiry_is_401_impersonation_expired(
    client: AsyncClient,
    make_trainer: MakeTrainer,
    impersonation_token: ImpersonationFactory,
    clock: FakeClock,
) -> None:
    inside = await impersonation_token(await make_trainer())
    outside = await impersonation_token(await make_trainer())

    clock.advance(minutes=60, seconds=-1)
    assert (await client.post(EXIT, headers=bearer(inside.access))).status_code == 204
    clock.advance(seconds=2)  # 60 min + 1 s after the start
    r = await client.post(EXIT, headers=bearer(outside.access))
    assert r.status_code == 401
    assert code(r) == "IMPERSONATION_EXPIRED"
