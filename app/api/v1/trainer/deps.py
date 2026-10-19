"""Route-level dependencies of the ``trainer`` router."""

from typing import Annotated, Any

from fastapi import Depends

from app.api.deps import (
    ClockDep,
    JobQueueDep,
    Principal,
    SessionDep,
    SettingsDep,
    StorageDep,
    TenantScope,
    rate_limit,
    require_role,
)
from app.core.errors import ForbiddenError
from app.core.ratelimit import RouteClass
from app.models.identity import UserRole
from app.services.availability_service import AvailabilityService
from app.services.branding_service import BrandingService
from app.services.sharelink_service import TrainerShareLinkService

by_principal = Depends(rate_limit(RouteClass.default_principal))

ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"description": "Not authenticated"},
    403: {"description": "Role not allowed"},
    422: {"description": "Validation error"},
    429: {"description": "RATE_LIMITED"},
}

_require_staff = require_role(UserRole.trainer, UserRole.coach)
_require_trainer = require_role(UserRole.trainer)


def _scope(principal: Principal) -> TenantScope:
    # Authentication guarantees a tenant for trainers and (non-moving) coaches; players and
    # super admins never get here because the role gate runs first.
    if principal.trainer_id is None:
        raise ForbiddenError()
    return TenantScope(
        trainer_id=principal.trainer_id, principal=principal, player_profile_ids=frozenset()
    )


def staff_scope(principal: Annotated[Principal, Depends(_require_staff)]) -> TenantScope:
    """Trainer or coach: their own tenant. Players cannot borrow one through ``?trainer_id``."""
    return _scope(principal)


def trainer_scope(principal: Annotated[Principal, Depends(_require_trainer)]) -> TenantScope:
    """Trainer only (coaches are read-only on most of this router)."""
    return _scope(principal)


StaffScopeDep = Annotated[TenantScope, Depends(staff_scope)]
TrainerScopeDep = Annotated[TenantScope, Depends(trainer_scope)]


def get_share_link_service(
    session: SessionDep, settings: SettingsDep, clock: ClockDep
) -> TrainerShareLinkService:
    return TrainerShareLinkService(session=session, settings=settings, clock=clock)


def get_availability_service(session: SessionDep) -> AvailabilityService:
    return AvailabilityService(session=session)


ShareLinkServiceDep = Annotated[TrainerShareLinkService, Depends(get_share_link_service)]
AvailabilityServiceDep = Annotated[AvailabilityService, Depends(get_availability_service)]


def get_branding_service(
    session: SessionDep, settings: SettingsDep, storage: StorageDep, jobs: JobQueueDep
) -> BrandingService:
    return BrandingService(session=session, settings=settings, storage=storage, jobs=jobs)


BrandingServiceDep = Annotated[BrandingService, Depends(get_branding_service)]
