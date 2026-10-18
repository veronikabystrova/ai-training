from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response

from app.api.deps import (
    ClockDep,
    FingerprintDep,
    HasherDep,
    IssuerDep,
    PrincipalDep,
    RateLimiterDep,
    SessionDep,
    SettingsDep,
    rate_limit,
)
from app.api.v1.auth.schemas import (
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    auth_response,
    token_pair,
)
from app.api.v1.common import AuthResponse, TokenPair
from app.core.ratelimit import RouteClass
from app.repositories.audit import AuditRepository
from app.services.audit_service import DbAuditService
from app.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


def get_auth_service(
    session: SessionDep,
    settings: SettingsDep,
    clock: ClockDep,
    hasher: HasherDep,
    issuer: IssuerDep,
    limiter: RateLimiterDep,
) -> AuthService:
    return AuthService(
        session=session,
        settings=settings,
        clock=clock,
        hasher=hasher,
        issuer=issuer,
        limiter=limiter,
        audit=DbAuditService(AuditRepository(session)),
    )


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]

_ERRORS: dict[int | str, dict[str, Any]] = {
    422: {"description": "Validation error"},
    429: {"description": "Rate limited"},
}


@router.post(
    "/login",
    response_model=AuthResponse,
    summary="Log in with email and password",
    dependencies=[Depends(rate_limit(RouteClass.login_ip))],
    responses={401: {"description": "INVALID_CREDENTIALS"}, **_ERRORS},
)
async def login(
    payload: LoginRequest, service: AuthServiceDep, client: FingerprintDep
) -> AuthResponse:
    return auth_response(
        await service.login(email=payload.email, password=payload.password, client=client)
    )


@router.post(
    "/refresh",
    response_model=TokenPair,
    summary="Rotate a refresh token",
    dependencies=[Depends(rate_limit(RouteClass.default_principal, by_ip=True))],
    responses={
        401: {
            "description": "INVALID_REFRESH_TOKEN, REFRESH_REUSE_DETECTED or IMPERSONATION_EXPIRED"
        },
        **_ERRORS,
    },
)
async def refresh(
    payload: RefreshRequest, service: AuthServiceDep, client: FingerprintDep
) -> TokenPair:
    return token_pair(await service.refresh(refresh_token=payload.refresh_token, client=client))


@router.post(
    "/logout",
    status_code=204,
    summary="Revoke the caller's refresh family",
    dependencies=[Depends(rate_limit(RouteClass.default_principal))],
    responses={
        401: {"description": "Not authenticated"},
        404: {"description": "NOT_FOUND"},
        **_ERRORS,
    },
)
async def logout(
    payload: LogoutRequest, principal: PrincipalDep, service: AuthServiceDep
) -> Response:
    await service.logout(user_id=principal.user_id, refresh_token=payload.refresh_token)
    return Response(status_code=204)
