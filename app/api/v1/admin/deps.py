"""Route-level dependencies of the ``admin`` router."""

from typing import Annotated, Any

from fastapi import Depends

from app.api.deps import (
    ClockDep,
    HasherDep,
    JobQueueDep,
    SessionDep,
    SettingsDep,
    rate_limit,
)
from app.core.ratelimit import RouteClass
from app.repositories.audit import AuditRepository
from app.services.admin_user_service import AdminUserService
from app.services.audit_service import DbAuditService

by_principal = Depends(rate_limit(RouteClass.default_principal))

ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"description": "Not authenticated"},
    403: {"description": "Not a super admin, or impersonating"},
    422: {"description": "Validation error"},
    429: {"description": "RATE_LIMITED"},
}


def get_admin_user_service(
    session: SessionDep,
    settings: SettingsDep,
    clock: ClockDep,
    hasher: HasherDep,
    jobs: JobQueueDep,
) -> AdminUserService:
    return AdminUserService(
        session=session,
        settings=settings,
        clock=clock,
        hasher=hasher,
        audit=DbAuditService(AuditRepository(session)),
        jobs=jobs,
    )


AdminUserServiceDep = Annotated[AdminUserService, Depends(get_admin_user_service)]
