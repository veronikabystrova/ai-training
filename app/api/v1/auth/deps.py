"""Dependencies shared by the ``auth`` and ``admin`` routers (impersonation start and exit)."""

from typing import Annotated

from fastapi import Depends

from app.api.deps import ClockDep, IssuerDep, SessionDep, SettingsDep
from app.repositories.audit import AuditRepository
from app.services.audit_service import DbAuditService
from app.services.impersonation_service import ImpersonationService


def get_impersonation_service(
    session: SessionDep, clock: ClockDep, settings: SettingsDep, issuer: IssuerDep
) -> ImpersonationService:
    return ImpersonationService(
        session=session,
        clock=clock,
        settings=settings,
        issuer=issuer,
        audit=DbAuditService(AuditRepository(session)),
    )


ImpersonationServiceDep = Annotated[ImpersonationService, Depends(get_impersonation_service)]
