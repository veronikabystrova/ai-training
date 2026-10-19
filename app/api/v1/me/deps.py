"""Route-level dependencies shared by the ``me`` routers."""

from typing import Any

from fastapi import Depends

from app.api.deps import Principal, PrincipalDep, Role, rate_limit
from app.core.errors import ForbiddenError
from app.core.ratelimit import RouteClass

by_principal = Depends(rate_limit(RouteClass.default_principal))

ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"description": "Not authenticated"},
    422: {"description": "Validation error"},
    429: {"description": "RATE_LIMITED"},
}


def require_parent(principal: PrincipalDep) -> Principal:
    """Player accounts that are not child logins (dependents, approvals, contexts)."""
    if principal.role is not Role.player or principal.child:
        raise ForbiddenError()
    return principal


def require_availability_owner(principal: PrincipalDep) -> Principal:
    """Players (child logins included, own profile) and coaches own an availability set."""
    if principal.role not in (Role.player, Role.coach):
        raise ForbiddenError()
    return principal
