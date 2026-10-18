"""Probe routes that return what each dependency resolved, for the contract tests."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from app.api.deps import (
    IdempotencyDep,
    OptionalPrincipalDep,
    PlayerContextDep,
    Principal,
    PrincipalDep,
    Role,
    SessionDep,
    SuperAdminScope,
    TenantDep,
    rate_limit,
    require_role,
)
from app.core.ratelimit import RouteClass

router = APIRouter(prefix="/probe")


def principal_json(p: Principal) -> dict[str, Any]:
    return {
        "user_id": str(p.user_id),
        "role": p.role.value,
        "trainer_id": str(p.trainer_id) if p.trainer_id else None,
        "act": str(p.act) if p.act else None,
        "child": p.child,
        "jti": str(p.jti),
    }


@router.get("/principal")
async def principal(p: PrincipalDep) -> dict[str, Any]:
    return principal_json(p)


@router.get("/optional")
async def optional(p: OptionalPrincipalDep) -> dict[str, Any] | None:
    return principal_json(p) if p else None


@router.get("/trainer-only")
async def trainer_only(p: Annotated[Principal, Depends(require_role(Role.trainer))]) -> str:
    return str(p.user_id)


@router.get("/tenant")
async def tenant(scope: TenantDep) -> dict[str, Any]:
    return {
        "trainer_id": str(scope.trainer_id),
        "player_profile_ids": sorted(str(i) for i in scope.player_profile_ids),
    }


@router.get("/player-context")
async def player_context(scope: PlayerContextDep) -> dict[str, Any]:
    return {
        "trainer_id": str(scope.trainer_id),
        "player_profile_ids": sorted(str(i) for i in scope.player_profile_ids),
    }


@router.get("/admin")
async def admin(p: SuperAdminScope) -> str:
    return str(p.user_id)


@router.post("/idempotent")
async def idempotent(
    request: Request, guard: IdempotencyDep, session: SessionDep
) -> dict[str, Any]:
    stored = await guard.claim()
    if stored is not None:
        return stored.body or {}
    effects: list[str] = request.app.state.effects
    effects.append("x")
    body: dict[str, Any] = {"effects": len(effects)}
    await guard.store(200, body)
    await session.commit()
    return body


@router.get("/limited", dependencies=[Depends(rate_limit(RouteClass.login_ip))])
async def limited() -> str:
    return "ok"


@router.get("/limited-principal", dependencies=[Depends(rate_limit(RouteClass.verify_resend_user))])
async def limited_principal() -> str:
    return "ok"
