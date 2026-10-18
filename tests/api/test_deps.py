from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.api.deps import rate_limit
from app.core.ratelimit import RouteClass
from app.core.security import TokenIssuer
from app.models.identity import (
    LinkStatus,
    User,
    UserRole,
    UserStatus,
)
from tests.api.helpers import bearer
from tests.factories import (
    TEST_JWT_SECRET,
    ImpersonationToken,
    LinkPlayer,
    MakeCoach,
    MakeDependent,
    MakePlayer,
    MakeTrainer,
    MakeUser,
    PrincipalToken,
)
from tests.fakes import FakeClock

ImpersonationFactory = Callable[..., Awaitable[ImpersonationToken]]


def code(response: Any) -> str:
    return str(response.json()["error"]["code"])


# --- current_principal ---------------------------------------------------------------------


async def test_missing_or_bad_token_is_401_invalid_token(client: AsyncClient) -> None:
    assert (await client.get("/probe/principal")).status_code == 401
    r = await client.get("/probe/principal", headers={"Authorization": "Bearer junk"})
    assert r.status_code == 401
    assert code(r) == "INVALID_TOKEN"
    r = await client.get("/probe/principal", headers={"Authorization": "Basic abc"})
    assert code(r) == "INVALID_TOKEN"


async def test_expired_token_is_401(
    client: AsyncClient, make_user: MakeUser, principal_token: PrincipalToken, clock: FakeClock
) -> None:
    tok = principal_token(await make_user(role=UserRole.player))
    assert (await client.get("/probe/principal", headers=bearer(tok))).status_code == 200
    clock.advance(seconds=900)
    assert (await client.get("/probe/principal", headers=bearer(tok))).status_code == 401


async def test_principal_fields_for_each_role(
    client: AsyncClient,
    make_user: MakeUser,
    make_trainer: MakeTrainer,
    make_coach: MakeCoach,
    principal_token: PrincipalToken,
) -> None:
    trainer = await make_trainer()
    coach = await make_coach(trainer)
    admin = await make_user(role=UserRole.super_admin)
    player = await make_user(role=UserRole.player)

    body = (await client.get("/probe/principal", headers=bearer(principal_token(trainer)))).json()
    assert (body["role"], body["trainer_id"], body["act"]) == ("trainer", str(trainer.id), None)

    coach_tok = principal_token(coach.user, trainer_id=trainer.id)
    body = (await client.get("/probe/principal", headers=bearer(coach_tok))).json()
    assert (body["role"], body["trainer_id"]) == ("coach", str(trainer.id))

    for user, role in ((admin, "super_admin"), (player, "player")):
        body = (await client.get("/probe/principal", headers=bearer(principal_token(user)))).json()
        assert (body["role"], body["trainer_id"], body["child"]) == (role, None, False)


@pytest.mark.parametrize("status", [UserStatus.inactive, UserStatus.pending, UserStatus.deleted])
async def test_inactive_user_rejected_immediately(
    client: AsyncClient,
    make_user: MakeUser,
    principal_token: PrincipalToken,
    db_session: AsyncSession,
    status: UserStatus,
) -> None:
    u = await make_user(role=UserRole.trainer)
    tok = principal_token(u)
    assert (await client.get("/probe/principal", headers=bearer(tok))).status_code == 200
    u.status = status
    await db_session.flush()
    r = await client.get("/probe/principal", headers=bearer(tok))
    assert r.status_code == 401  # A-03
    assert code(r) == "INVALID_TOKEN"


async def test_unknown_user_is_401(
    client: AsyncClient,
    make_user: MakeUser,
    principal_token: PrincipalToken,
    db_session: AsyncSession,
) -> None:
    ghost = User(id=uuid4(), email="g@x.com", password_hash="x", role=UserRole.player)
    r = await client.get("/probe/principal", headers=bearer(principal_token(ghost)))
    assert r.status_code == 401


async def test_coach_moved_tenant_token_is_stale(
    client: AsyncClient,
    make_trainer: MakeTrainer,
    make_coach: MakeCoach,
    principal_token: PrincipalToken,
    db_session: AsyncSession,
    clock: FakeClock,
) -> None:
    t1, t2 = await make_trainer(), await make_trainer()
    tenure = await make_coach(t1)
    old = principal_token(tenure.user, trainer_id=t1.id)
    assert (await client.get("/probe/principal", headers=bearer(old))).status_code == 200

    tenure.is_active, tenure.left_at = False, clock.now()
    await db_session.flush()
    await make_coach(t2, user=tenure.user)

    r = await client.get("/probe/principal", headers=bearer(old))
    assert (r.status_code, code(r)) == (401, "INVALID_TOKEN")
    fresh = principal_token(tenure.user, trainer_id=t2.id)
    body = (await client.get("/probe/principal", headers=bearer(fresh))).json()
    assert body["trainer_id"] == str(t2.id)


async def test_coach_without_active_tenure_is_401(
    client: AsyncClient,
    make_trainer: MakeTrainer,
    make_coach: MakeCoach,
    principal_token: PrincipalToken,
    db_session: AsyncSession,
    clock: FakeClock,
) -> None:
    trainer = await make_trainer()
    tenure = await make_coach(trainer)
    tenure.is_active, tenure.left_at = False, clock.now()
    await db_session.flush()
    r = await client.get(
        "/probe/principal", headers=bearer(principal_token(tenure.user, trainer_id=trainer.id))
    )
    assert (r.status_code, code(r)) == (401, "INVALID_TOKEN")


async def test_deactivated_trainer_tenant_is_404_for_coach(
    client: AsyncClient,
    make_trainer: MakeTrainer,
    make_coach: MakeCoach,
    principal_token: PrincipalToken,
    db_session: AsyncSession,
) -> None:
    trainer = await make_trainer()
    tenure = await make_coach(trainer)
    tok = principal_token(tenure.user, trainer_id=trainer.id)
    assert (await client.get("/probe/principal", headers=bearer(tok))).status_code == 200
    trainer.status = UserStatus.inactive
    await db_session.flush()
    r = await client.get("/probe/principal", headers=bearer(tok))
    assert (r.status_code, code(r)) == (404, "NOT_FOUND")  # G-15


async def test_optional_principal(
    client: AsyncClient, make_user: MakeUser, principal_token: PrincipalToken
) -> None:
    assert (await client.get("/probe/optional")).json() is None
    tok = principal_token(await make_user(role=UserRole.player))
    assert (await client.get("/probe/optional", headers=bearer(tok))).json()["role"] == "player"
    r = await client.get("/probe/optional", headers={"Authorization": "Bearer junk"})
    assert (r.status_code, code(r)) == (401, "INVALID_TOKEN")


async def test_principal_resolution_is_one_select_without_is_true(
    client: AsyncClient,
    engine: AsyncEngine,
    make_trainer: MakeTrainer,
    make_coach: MakeCoach,
    principal_token: PrincipalToken,
) -> None:
    trainer = await make_trainer()
    tenure = await make_coach(trainer)
    tok = principal_token(tenure.user, trainer_id=trainer.id)
    statements: list[str] = []

    def record(_c: Any, _cur: Any, statement: str, *_a: Any) -> None:
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", record)
    try:
        r = await client.get("/probe/principal", headers=bearer(tok))
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", record)
    assert r.status_code == 200
    selects = [s for s in statements if s.lstrip().upper().startswith("SELECT")]
    assert len(selects) == 1  # A-03: one round trip
    assert "IS true" not in selects[0]  # keeps the partial indexes usable


# --- impersonation -------------------------------------------------------------------------


async def test_live_impersonation_session_sets_act(
    client: AsyncClient, make_trainer: MakeTrainer, impersonation_token: ImpersonationFactory
) -> None:
    trainer = await make_trainer()
    imp = await impersonation_token(trainer)
    r = await client.get("/probe/principal", headers=bearer(imp.access))
    assert r.status_code == 200
    assert r.json()["act"] == str(imp.admin_id)
    assert r.json()["jti"] == str(imp.jti)


async def test_expired_or_ended_impersonation_session_is_401(
    client: AsyncClient,
    make_trainer: MakeTrainer,
    impersonation_token: ImpersonationFactory,
    db_session: AsyncSession,
    clock: FakeClock,
) -> None:
    trainer = await make_trainer()
    imp = await impersonation_token(trainer, minutes=60)
    headers = bearer(imp.access)
    assert (await client.get("/probe/principal", headers=headers)).status_code == 200

    clock.advance(minutes=60)  # expires_at <= now
    r = await client.get("/probe/principal", headers=headers)
    assert (r.status_code, code(r)) == (401, "IMPERSONATION_EXPIRED")

    clock.advance(minutes=-30)
    assert (await client.get("/probe/principal", headers=headers)).status_code == 200
    imp.session.ended_at = clock.now()
    await db_session.flush()
    r = await client.get("/probe/principal", headers=headers)
    assert (r.status_code, code(r)) == (401, "IMPERSONATION_EXPIRED")


async def test_impersonation_ends_when_the_admin_is_deactivated(
    client: AsyncClient,
    make_trainer: MakeTrainer,
    make_user: MakeUser,
    impersonation_token: ImpersonationFactory,
    db_session: AsyncSession,
) -> None:
    admin = await make_user(role=UserRole.super_admin)
    imp = await impersonation_token(await make_trainer(), admin=admin)
    headers = bearer(imp.access)
    assert (await client.get("/probe/principal", headers=headers)).status_code == 200
    admin.status = UserStatus.inactive
    await db_session.flush()
    r = await client.get("/probe/principal", headers=headers)
    assert (r.status_code, code(r)) == (401, "IMPERSONATION_EXPIRED")


async def test_act_claim_without_session_is_401_impersonation_expired(
    client: AsyncClient,
    make_trainer: MakeTrainer,
    make_user: MakeUser,
    principal_token: PrincipalToken,
) -> None:
    trainer = await make_trainer()
    admin = await make_user(role=UserRole.super_admin)
    r = await client.get("/probe/principal", headers=bearer(principal_token(trainer, act=admin)))
    assert (r.status_code, code(r)) == (401, "IMPERSONATION_EXPIRED")


async def test_impersonation_session_of_another_target_is_rejected(
    client: AsyncClient,
    make_trainer: MakeTrainer,
    make_user: MakeUser,
    impersonation_token: ImpersonationFactory,
    clock: FakeClock,
) -> None:
    a, b = await make_trainer(), await make_trainer()
    imp = await impersonation_token(a)
    admin = await make_user(role=UserRole.super_admin)
    forged, _ = TokenIssuer(secret=TEST_JWT_SECRET, access_ttl=900, clock=clock).issue_access(
        user_id=b.id, role="trainer", trainer_id=b.id, act=admin.id, child=False, jti=imp.jti
    )  # b's token reusing a's session jti
    r = await client.get("/probe/principal", headers=bearer(forged))
    assert (r.status_code, code(r)) == (401, "IMPERSONATION_EXPIRED")


# --- require_role / SuperAdminScope --------------------------------------------------------


async def test_require_role_403(
    client: AsyncClient,
    make_trainer: MakeTrainer,
    make_coach: MakeCoach,
    principal_token: PrincipalToken,
) -> None:
    trainer = await make_trainer()
    tenure = await make_coach(trainer)
    coach_tok = principal_token(tenure.user, trainer_id=trainer.id)
    r = await client.get("/probe/trainer-only", headers=bearer(coach_tok))
    assert (r.status_code, code(r)) == (403, "FORBIDDEN")
    trainer_tok = principal_token(trainer)
    assert (await client.get("/probe/trainer-only", headers=bearer(trainer_tok))).status_code == 200


async def test_super_admin_scope(
    client: AsyncClient,
    make_user: MakeUser,
    make_trainer: MakeTrainer,
    principal_token: PrincipalToken,
) -> None:
    admin = await make_user(role=UserRole.super_admin)
    assert (
        await client.get("/probe/admin", headers=bearer(principal_token(admin)))
    ).status_code == 200
    trainer_tok = principal_token(await make_trainer())
    assert (await client.get("/probe/admin", headers=bearer(trainer_tok))).status_code == 403


async def test_super_admin_scope_rejects_impersonation(
    client: AsyncClient, make_user: MakeUser, impersonation_token: ImpersonationFactory
) -> None:
    # Even a (never issued) impersonation of a super_admin cannot reach admin scope: act is set.
    target = await make_user(role=UserRole.super_admin)
    imp = await impersonation_token(target)
    r = await client.get("/probe/admin", headers=bearer(imp.access))
    assert (r.status_code, code(r)) == (403, "FORBIDDEN")


# --- require_tenant_scope / player_context -------------------------------------------------


async def test_tenant_scope_for_trainer_and_coach_ignores_query(
    client: AsyncClient,
    make_trainer: MakeTrainer,
    make_coach: MakeCoach,
    principal_token: PrincipalToken,
) -> None:
    trainer, other = await make_trainer(), await make_trainer()
    tenure = await make_coach(trainer)
    for tok in (principal_token(trainer), principal_token(tenure.user, trainer_id=trainer.id)):
        r = await client.get(
            "/probe/tenant", params={"trainer_id": str(other.id)}, headers=bearer(tok)
        )
        assert r.json() == {"trainer_id": str(trainer.id), "player_profile_ids": []}


async def test_tenant_scope_rejects_super_admin(
    client: AsyncClient, make_user: MakeUser, principal_token: PrincipalToken
) -> None:
    admin = await make_user(role=UserRole.super_admin)
    r = await client.get("/probe/tenant", headers=bearer(principal_token(admin)))
    assert (r.status_code, code(r)) == (403, "FORBIDDEN")


async def test_player_context_requires_active_link_else_404(
    client: AsyncClient,
    make_player: MakePlayer,
    make_trainer: MakeTrainer,
    link_player: LinkPlayer,
    principal_token: PrincipalToken,
    db_session: AsyncSession,
) -> None:
    trainer = await make_trainer()
    profile = await make_player()
    owner = await db_session.get(User, profile.owner_user_id)
    assert owner is not None
    headers = bearer(principal_token(owner))
    params = {"trainer_id": str(trainer.id)}

    for path in ("/probe/player-context", "/probe/tenant"):
        r = await client.get(path, params=params, headers=headers)
        assert (r.status_code, code(r)) == (404, "NOT_FOUND")  # no link: never 403

    link = await link_player(trainer, profile)
    for path in ("/probe/player-context", "/probe/tenant"):
        r = await client.get(path, params=params, headers=headers)
        assert r.status_code == 200
        assert r.json() == {"trainer_id": str(trainer.id), "player_profile_ids": [str(profile.id)]}

    link.status = LinkStatus.inactive
    await db_session.flush()
    r = await client.get("/probe/player-context", params=params, headers=headers)
    assert r.status_code == 404


async def test_player_context_misses_are_404(
    client: AsyncClient,
    make_player: MakePlayer,
    make_trainer: MakeTrainer,
    link_player: LinkPlayer,
    principal_token: PrincipalToken,
    db_session: AsyncSession,
) -> None:
    trainer, other = await make_trainer(), await make_trainer()
    profile = await make_player()
    await link_player(trainer, profile)
    owner = await db_session.get(User, profile.owner_user_id)
    assert owner is not None
    headers = bearer(principal_token(owner))

    r = await client.get(
        "/probe/player-context", params={"trainer_id": str(other.id)}, headers=headers
    )
    assert r.status_code == 404  # linked to a different trainer only
    r = await client.get("/probe/tenant", headers=headers)  # player without a context
    assert r.status_code == 404
    r = await client.get("/probe/player-context", headers=headers)  # required query
    assert (r.status_code, code(r)) == (422, "VALIDATION_ERROR")

    trainer.status = UserStatus.inactive
    await db_session.flush()
    r = await client.get(
        "/probe/player-context", params={"trainer_id": str(trainer.id)}, headers=headers
    )
    assert r.status_code == 404


async def test_player_context_for_non_player_is_404(
    client: AsyncClient, make_trainer: MakeTrainer, principal_token: PrincipalToken
) -> None:
    trainer = await make_trainer()
    r = await client.get(
        "/probe/player-context",
        params={"trainer_id": str(trainer.id)},
        headers=bearer(principal_token(trainer)),
    )
    assert r.status_code == 404


async def test_player_profile_ids_parent_sees_dependents_and_child_only_itself(
    client: AsyncClient,
    make_user: MakeUser,
    make_player: MakePlayer,
    make_dependent: MakeDependent,
    make_trainer: MakeTrainer,
    link_player: LinkPlayer,
    principal_token: PrincipalToken,
    db_session: AsyncSession,
) -> None:
    trainer = await make_trainer()
    parent = await make_user(role=UserRole.player)
    own = await make_player(owner=parent)
    kid = await make_dependent(parent=parent, with_login=True)
    unlinked = await make_dependent(parent=parent)
    for profile in (own, kid):
        await link_player(trainer, profile)
    params = {"trainer_id": str(trainer.id)}

    r = await client.get(
        "/probe/player-context", params=params, headers=bearer(principal_token(parent))
    )
    assert r.json()["player_profile_ids"] == sorted(str(p.id) for p in (own, kid))
    assert str(unlinked.id) not in r.text

    child_user = await db_session.get(User, kid.login_user_id)
    assert child_user is not None
    r = await client.get(
        "/probe/player-context",
        params=params,
        headers=bearer(principal_token(child_user, child=True)),
    )
    assert r.json()["player_profile_ids"] == [str(kid.id)]
    assert r.json()  # child flag honoured
    body = (
        await client.get(
            "/probe/principal", headers=bearer(principal_token(child_user, child=True))
        )
    ).json()
    assert body["child"] is True


# --- idempotency ---------------------------------------------------------------------------


async def test_idempotency_key_required_and_bounded(
    client: AsyncClient, make_user: MakeUser, principal_token: PrincipalToken
) -> None:
    headers = bearer(principal_token(await make_user(role=UserRole.player)))
    r = await client.post("/probe/idempotent", json={}, headers=headers)
    assert (r.status_code, code(r)) == (400, "IDEMPOTENCY_KEY_REQUIRED")
    r = await client.post(
        "/probe/idempotent", json={}, headers={**headers, "Idempotency-Key": "k" * 65}
    )
    assert (r.status_code, code(r)) == (422, "VALIDATION_ERROR")


async def test_idempotency_replays_stored_response_and_runs_once(
    client: AsyncClient, app: FastAPI, make_user: MakeUser, principal_token: PrincipalToken
) -> None:
    headers = {
        **bearer(principal_token(await make_user(role=UserRole.player))),
        "Idempotency-Key": "k1",
    }
    first = await client.post("/probe/idempotent", json={"a": 1}, headers=headers)
    second = await client.post("/probe/idempotent", json={"a": 1}, headers=headers)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json() == {"effects": 1}
    assert len(app.state.effects) == 1


async def test_idempotency_key_reused_with_other_body_is_409(
    client: AsyncClient, make_user: MakeUser, principal_token: PrincipalToken
) -> None:
    headers = {
        **bearer(principal_token(await make_user(role=UserRole.player))),
        "Idempotency-Key": "k2",
    }
    assert (
        await client.post("/probe/idempotent", json={"a": 1}, headers=headers)
    ).status_code == 200
    r = await client.post("/probe/idempotent", json={"a": 2}, headers=headers)
    assert (r.status_code, code(r)) == (409, "IDEMPOTENCY_KEY_REUSED")


async def test_idempotency_key_is_scoped_by_principal(
    client: AsyncClient, app: FastAPI, make_user: MakeUser, principal_token: PrincipalToken
) -> None:
    a = bearer(principal_token(await make_user(role=UserRole.player)))
    b = bearer(principal_token(await make_user(role=UserRole.player)))
    for headers in (a, b):
        r = await client.post(
            "/probe/idempotent", json={}, headers={**headers, "Idempotency-Key": "same"}
        )
        assert r.status_code == 200
    assert len(app.state.effects) == 2


# --- rate_limit ----------------------------------------------------------------------------


async def test_ip_rate_limit_429_with_retry_after(client: AsyncClient) -> None:
    for _ in range(10):  # login_ip is 10/minute
        assert (await client.get("/probe/limited")).status_code == 200
    r = await client.get("/probe/limited")
    assert (r.status_code, code(r)) == (429, "RATE_LIMITED")
    assert int(r.headers["Retry-After"]) >= 1


async def test_principal_rate_limit_is_per_user(
    client: AsyncClient, make_user: MakeUser, principal_token: PrincipalToken
) -> None:
    a = bearer(principal_token(await make_user(role=UserRole.player)))
    b = bearer(principal_token(await make_user(role=UserRole.player)))
    for _ in range(3):  # verify_resend_user is 3/hour
        assert (await client.get("/probe/limited-principal", headers=a)).status_code == 200
    assert (await client.get("/probe/limited-principal", headers=a)).status_code == 429
    assert (await client.get("/probe/limited-principal", headers=b)).status_code == 200


def test_email_keyed_route_classes_are_not_dependencies() -> None:
    for rc in (RouteClass.login_email, RouteClass.password_reset_email):
        with pytest.raises(ValueError, match="keyed by email"):
            rate_limit(rc)
