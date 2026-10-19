"""Route-level dependencies shared by the ``me`` routers."""

from typing import Annotated, Any

from fastapi import Depends

from app.api.deps import (
    ClockDep,
    CommitmentCheckerDep,
    HasherDep,
    JobQueueDep,
    PasswordPolicyDep,
    Principal,
    PrincipalDep,
    PurchaseExecutorDep,
    Role,
    SessionDep,
    SettingsDep,
    rate_limit,
)
from app.core.errors import ForbiddenError
from app.core.ratelimit import RouteClass
from app.services.approval_service import ApprovalService
from app.services.availability_service import AvailabilityService
from app.services.player_service import PlayerService

by_principal = Depends(rate_limit(RouteClass.default_principal))

ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"description": "Not authenticated"},
    422: {"description": "Validation error"},
    429: {"description": "RATE_LIMITED"},
}


def require_player(principal: PrincipalDep) -> Principal:
    """Any player login: a parent or a child (children may read their own profile)."""
    if principal.role is not Role.player:
        raise ForbiddenError()
    return principal


def require_parent(principal: PrincipalDep) -> Principal:
    """Player accounts that are not child logins (dependents, approvals, contexts)."""
    if principal.role is not Role.player or principal.child:
        raise ForbiddenError()
    return principal


def require_dependent_creator(principal: PrincipalDep) -> Principal:
    if principal.role is Role.player and principal.child:
        raise ForbiddenError("CHILD_CANNOT_CREATE_DEPENDENT", "A child cannot create dependents")
    return require_parent(principal)


def require_availability_owner(principal: PrincipalDep) -> Principal:
    """Players (child logins included, own profile) and coaches own an availability set."""
    if principal.role not in (Role.player, Role.coach):
        raise ForbiddenError()
    return principal


PlayerCallerDep = Annotated[Principal, Depends(require_player)]
ParentDep = Annotated[Principal, Depends(require_parent)]
DependentCreatorDep = Annotated[Principal, Depends(require_dependent_creator)]
AvailabilityOwnerDep = Annotated[Principal, Depends(require_availability_owner)]


def get_availability_service(session: SessionDep) -> AvailabilityService:
    return AvailabilityService(session=session)


def get_player_service(
    session: SessionDep,
    settings: SettingsDep,
    clock: ClockDep,
    hasher: HasherDep,
    policy: PasswordPolicyDep,
    commitments: CommitmentCheckerDep,
) -> PlayerService:
    return PlayerService(
        session=session,
        settings=settings,
        clock=clock,
        hasher=hasher,
        policy=policy,
        commitments=commitments,
    )


AvailabilityServiceDep = Annotated[AvailabilityService, Depends(get_availability_service)]
PlayerServiceDep = Annotated[PlayerService, Depends(get_player_service)]


def get_approval_service(
    session: SessionDep, clock: ClockDep, executor: PurchaseExecutorDep, jobs: JobQueueDep
) -> ApprovalService:
    return ApprovalService(session=session, clock=clock, executor=executor, jobs=jobs)


ApprovalServiceDep = Annotated[ApprovalService, Depends(get_approval_service)]
