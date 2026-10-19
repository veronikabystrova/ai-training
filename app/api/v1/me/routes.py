from typing import Annotated

from fastapi import APIRouter, Depends, Response

from app.api.deps import (
    ClockDep,
    HasherDep,
    JobQueueDep,
    PrincipalDep,
    SessionDep,
    SettingsDep,
    StorageDep,
    rate_limit,
)
from app.api.v1.auth.schemas import StatusResponse
from app.api.v1.me.deps import (
    ERRORS,
    AvailabilityOwnerDep,
    AvailabilityServiceDep,
    ParentDep,
    PlayerServiceDep,
    by_principal,
)
from app.api.v1.me.schemas import (
    AvailabilitySet,
    AvatarConfirmRequest,
    AvatarPresignRequest,
    EmailChangeConfirm,
    EmailChangeRequest,
    MeResponse,
    PlayerContext,
    PresignResponse,
    UpdateMe,
    availability_response,
    context_response,
    me_response,
)
from app.core.ratelimit import RouteClass
from app.repositories.audit import AuditRepository
from app.services.audit_service import DbAuditService
from app.services.user_service import UserService

router = APIRouter(prefix="/me", tags=["me"])

_by_ip = Depends(rate_limit(RouteClass.default_principal, by_ip=True))


def get_user_service(
    session: SessionDep,
    settings: SettingsDep,
    clock: ClockDep,
    hasher: HasherDep,
    storage: StorageDep,
    jobs: JobQueueDep,
) -> UserService:
    return UserService(
        session=session,
        settings=settings,
        clock=clock,
        hasher=hasher,
        storage=storage,
        audit=DbAuditService(AuditRepository(session)),
        jobs=jobs,
    )


UserServiceDep = Annotated[UserService, Depends(get_user_service)]


@router.get(
    "",
    response_model=MeResponse,
    summary="Current account",
    dependencies=[by_principal],
    responses={401: ERRORS[401], 404: {"description": "Trainer tenant deactivated"}},
)
async def get_me(principal: PrincipalDep, service: UserServiceDep) -> MeResponse:
    return me_response(await service.get_me(principal.user_id), principal)


@router.patch(
    "",
    response_model=MeResponse,
    summary="Update the current profile",
    dependencies=[by_principal],
    responses={**ERRORS, 422: {"description": "VALIDATION_ERROR or FIELD_NOT_ALLOWED"}},
)
async def patch_me(
    payload: UpdateMe, principal: PrincipalDep, service: UserServiceDep
) -> MeResponse:
    return me_response(await service.update_me(principal.user_id, payload.changes()), principal)


@router.put(
    "/avatar",
    response_model=PresignResponse,
    summary="Presign an avatar upload",
    dependencies=[by_principal],
    responses=ERRORS,
)
async def presign_avatar(
    payload: AvatarPresignRequest, principal: PrincipalDep, service: UserServiceDep
) -> PresignResponse:
    key, presigned = await service.presign_avatar(
        content_type=payload.content_type, content_length=payload.content_length
    )
    return PresignResponse.model_validate(
        {
            "upload_url": presigned.url,
            "key": key,
            "expires_in": presigned.expires_in,
            "required_headers": presigned.headers,
        }
    )


@router.post(
    "/avatar/confirm",
    response_model=MeResponse,
    summary="Confirm an uploaded avatar",
    dependencies=[by_principal],
    responses={
        **ERRORS,
        404: {"description": "Key not found in storage"},
        422: {"description": "INVALID_AVATAR or VALIDATION_ERROR"},
    },
)
async def confirm_avatar(
    payload: AvatarConfirmRequest, principal: PrincipalDep, service: UserServiceDep
) -> MeResponse:
    return me_response(
        await service.confirm_avatar(principal.user_id, key=payload.avatar_key), principal
    )


@router.post(
    "/email-change",
    status_code=202,
    response_model=StatusResponse,
    summary="Request an email change",
    dependencies=[by_principal],
    responses={
        **ERRORS,
        403: {"description": "INVALID_CURRENT_PASSWORD"},
        409: {"description": "EMAIL_TAKEN"},
    },
)
async def request_email_change(
    payload: EmailChangeRequest, principal: PrincipalDep, service: UserServiceDep
) -> StatusResponse:
    await service.request_email_change(
        principal.user_id, new_email=payload.new_email, password=payload.password
    )
    return StatusResponse(status="accepted")


@router.post(
    "/email-change/confirm",
    status_code=204,
    summary="Confirm an email change",
    dependencies=[_by_ip],
    responses={
        400: {"description": "TOKEN_INVALID"},
        409: {"description": "EMAIL_TAKEN"},
        422: ERRORS[422],
        429: ERRORS[429],
    },
)
async def confirm_email_change(payload: EmailChangeConfirm, service: UserServiceDep) -> Response:
    await service.confirm_email_change(token=payload.token)
    return Response(status_code=204)


@router.get(
    "/availability",
    response_model=AvailabilitySet,
    summary="Own weekly availability",
    dependencies=[by_principal],
    responses={401: ERRORS[401], 403: {"description": "Not a player or coach"}},
)
async def get_availability(
    principal: AvailabilityOwnerDep, service: AvailabilityServiceDep
) -> AvailabilitySet:
    view = await service.get_own(
        user_id=principal.user_id, role=principal.role, child=principal.child
    )
    return availability_response(view)


@router.put(
    "/availability",
    response_model=AvailabilitySet,
    summary="Replace own weekly availability",
    dependencies=[by_principal],
    responses={
        **ERRORS,
        403: {"description": "Not a player or coach"},
        422: {"description": "OVERLAPPING_WINDOWS or VALIDATION_ERROR"},
    },
)
async def put_availability(
    payload: AvailabilitySet, principal: AvailabilityOwnerDep, service: AvailabilityServiceDep
) -> AvailabilitySet:
    view = await service.replace_own(
        user_id=principal.user_id,
        role=principal.role,
        child=principal.child,
        timezone=payload.timezone,
        windows=payload.specs(),
    )
    return availability_response(view)


@router.get(
    "/contexts",
    response_model=list[PlayerContext],
    summary="Trainer contexts the caller may act in",
    dependencies=[by_principal],
    responses={401: ERRORS[401], 403: {"description": "Not a parent account"}},
)
async def get_contexts(principal: ParentDep, service: PlayerServiceDep) -> list[PlayerContext]:
    return [context_response(c) for c in await service.contexts(user_id=principal.user_id)]
