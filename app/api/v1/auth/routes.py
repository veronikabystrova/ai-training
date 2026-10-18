from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response

from app.api.deps import (
    ClockDep,
    FingerprintDep,
    HasherDep,
    IssuerDep,
    JobQueueDep,
    PasswordPolicyDep,
    PrincipalDep,
    RateLimiterDep,
    SessionDep,
    SettingsDep,
    rate_limit,
)
from app.api.v1.auth.schemas import (
    LoginRequest,
    LogoutRequest,
    PasswordResetConfirm,
    PasswordResetRequest,
    PasswordSetupRequest,
    RefreshRequest,
    StatusResponse,
    VerifyEmailRequest,
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
    policy: PasswordPolicyDep,
    jobs: JobQueueDep,
) -> AuthService:
    return AuthService(
        session=session,
        settings=settings,
        clock=clock,
        hasher=hasher,
        issuer=issuer,
        limiter=limiter,
        audit=DbAuditService(AuditRepository(session)),
        policy=policy,
        jobs=jobs,
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


_default_by_ip = Depends(rate_limit(RouteClass.default_principal, by_ip=True))


@router.post(
    "/password-reset/request",
    status_code=202,
    response_model=StatusResponse,
    summary="Request a password-reset email",
    responses=_ERRORS,
)
async def password_reset_request(
    payload: PasswordResetRequest, service: AuthServiceDep
) -> StatusResponse:
    await service.request_password_reset(email=payload.email)
    return StatusResponse(status="accepted")


@router.post(
    "/password-reset/confirm",
    status_code=204,
    summary="Set a new password with a reset token",
    dependencies=[_default_by_ip],
    responses={400: {"description": "TOKEN_INVALID"}, **_ERRORS},
)
async def password_reset_confirm(
    payload: PasswordResetConfirm, service: AuthServiceDep
) -> Response:
    await service.confirm_password_reset(token=payload.token, new_password=payload.new_password)
    return Response(status_code=204)


@router.post(
    "/verify-email",
    status_code=204,
    summary="Verify an email address with a token",
    dependencies=[_default_by_ip],
    responses={400: {"description": "TOKEN_INVALID"}, **_ERRORS},
)
async def verify_email(payload: VerifyEmailRequest, service: AuthServiceDep) -> Response:
    await service.verify_email(token=payload.token)
    return Response(status_code=204)


@router.post(
    "/verify-email/resend",
    status_code=202,
    response_model=StatusResponse,
    summary="Resend the email verification link",
    dependencies=[Depends(rate_limit(RouteClass.verify_resend_user))],
    responses={
        200: {"description": "already_verified"},
        401: {"description": "Not authenticated"},
        429: _ERRORS[429],
    },
)
async def verify_email_resend(
    principal: PrincipalDep, service: AuthServiceDep, response: Response
) -> StatusResponse:
    if await service.resend_email_verification(user_id=principal.user_id):
        return StatusResponse(status="accepted")
    response.status_code = 200
    return StatusResponse(status="already_verified")


@router.post(
    "/password/setup",
    response_model=AuthResponse,
    summary="Activate a provisioned trainer account",
    dependencies=[_default_by_ip],
    responses={400: {"description": "TOKEN_INVALID"}, **_ERRORS},
)
async def password_setup(
    payload: PasswordSetupRequest, service: AuthServiceDep, client: FingerprintDep
) -> AuthResponse:
    return auth_response(
        await service.setup_password(
            token=payload.token, new_password=payload.new_password, client=client
        )
    )
