from uuid import UUID

from fastapi import APIRouter, Response

from app.api.deps import SettingsDep
from app.api.v1.trainer.deps import (
    ERRORS,
    AvailabilityServiceDep,
    BrandingServiceDep,
    ShareLinkServiceDep,
    StaffScopeDep,
    TrainerScopeDep,
    by_principal,
)
from app.api.v1.trainer.schemas import (
    AvailabilityRead,
    BrandingResponse,
    CreateShareLink,
    LogoConfirmRequest,
    LogoPresignRequest,
    LogoPresignResponse,
    ShareLinkResponse,
    UpdateBranding,
    availability_read,
    branding_response,
    share_link_response,
)
from app.core.pagination import CursorQ, LimitQ, Page
from app.models.identity import ShareLinkKind

router = APIRouter(prefix="/trainer", tags=["trainer"])


@router.post(
    "/share-links",
    status_code=201,
    response_model=ShareLinkResponse,
    summary="Create a share link",
    dependencies=[by_principal],
    responses={
        **ERRORS,
        403: {"description": "A coach cannot create coach_unique links"},
        409: {"description": "COACH_ALREADY_ASSIGNED"},
    },
)
async def create_share_link(
    payload: CreateShareLink,
    scope: StaffScopeDep,
    service: ShareLinkServiceDep,
    settings: SettingsDep,
) -> ShareLinkResponse:
    link = await service.create(
        trainer_id=scope.trainer_id,
        creator_id=scope.principal.user_id,
        role=scope.principal.role,
        kind=payload.kind,
        target_email=payload.target_email,
    )
    return share_link_response(link, settings)


@router.get(
    "/share-links",
    response_model=Page[ShareLinkResponse],
    summary="List the tenant's share links",
    dependencies=[by_principal],
    responses=ERRORS,
)
async def list_share_links(
    scope: StaffScopeDep,
    service: ShareLinkServiceDep,
    settings: SettingsDep,
    kind: ShareLinkKind | None = None,
    is_active: bool | None = None,
    limit: LimitQ = 50,
    cursor: CursorQ = None,
) -> Page[ShareLinkResponse]:
    links, next_cursor = await service.list_page(
        trainer_id=scope.trainer_id, kind=kind, is_active=is_active, cursor=cursor, limit=limit
    )
    return Page[ShareLinkResponse](
        items=[share_link_response(link, settings) for link in links], next_cursor=next_cursor
    )


@router.delete(
    "/share-links/{share_link_id}",
    status_code=204,
    summary="Deactivate a share link",
    dependencies=[by_principal],
    responses={**ERRORS, 404: {"description": "Unknown or another tenant's link"}},
)
async def deactivate_share_link(
    share_link_id: UUID, scope: TrainerScopeDep, service: ShareLinkServiceDep
) -> Response:
    await service.deactivate(trainer_id=scope.trainer_id, share_link_id=share_link_id)
    return Response(status_code=204)


@router.get(
    "/players/{player_profile_id}/availability",
    response_model=AvailabilityRead,
    summary="A linked player's weekly availability",
    dependencies=[by_principal],
    responses={**ERRORS, 404: {"description": "No active link to this tenant"}},
)
async def get_player_availability(
    player_profile_id: UUID, scope: StaffScopeDep, service: AvailabilityServiceDep
) -> AvailabilityRead:
    return availability_read(
        await service.read_player_for_trainer(
            trainer_id=scope.trainer_id, player_profile_id=player_profile_id
        )
    )


@router.get(
    "/coaches/{coach_user_id}/availability",
    response_model=AvailabilityRead,
    summary="An active coach's weekly availability",
    dependencies=[by_principal],
    responses={**ERRORS, 404: {"description": "Not an active coach of this tenant"}},
)
async def get_coach_availability(
    coach_user_id: UUID, scope: TrainerScopeDep, service: AvailabilityServiceDep
) -> AvailabilityRead:
    return availability_read(
        await service.read_coach_for_trainer(
            trainer_id=scope.trainer_id, coach_user_id=coach_user_id
        )
    )


@router.get(
    "/branding",
    response_model=BrandingResponse,
    summary="The tenant's branding",
    dependencies=[by_principal],
    responses=ERRORS,
)
async def get_branding(scope: StaffScopeDep, service: BrandingServiceDep) -> BrandingResponse:
    return branding_response(await service.get(scope.trainer_id))


@router.patch(
    "/branding",
    response_model=BrandingResponse,
    summary="Update the tenant's branding",
    dependencies=[by_principal],
    responses=ERRORS,
)
async def patch_branding(
    payload: UpdateBranding, scope: TrainerScopeDep, service: BrandingServiceDep
) -> BrandingResponse:
    return branding_response(await service.update(scope.trainer_id, payload.changes()))


@router.post(
    "/branding/logo",
    response_model=LogoPresignResponse,
    summary="Presign a logo upload",
    dependencies=[by_principal],
    responses=ERRORS,
)
async def presign_logo(
    payload: LogoPresignRequest, scope: TrainerScopeDep, service: BrandingServiceDep
) -> LogoPresignResponse:
    key, presigned = await service.presign_logo(
        scope.trainer_id, content_type=payload.content_type, content_length=payload.content_length
    )
    return LogoPresignResponse.model_validate(
        {
            "upload_url": presigned.url,
            "key": key,
            "expires_in": presigned.expires_in,
            "required_headers": presigned.headers,
        }
    )


@router.post(
    "/branding/logo/confirm",
    response_model=BrandingResponse,
    summary="Confirm an uploaded logo",
    dependencies=[by_principal],
    responses={
        **ERRORS,
        404: {"description": "Key not issued to this trainer or not found in storage"},
        422: {"description": "INVALID_LOGO or VALIDATION_ERROR"},
    },
)
async def confirm_logo(
    payload: LogoConfirmRequest, scope: TrainerScopeDep, service: BrandingServiceDep
) -> BrandingResponse:
    return branding_response(await service.confirm_logo(scope.trainer_id, key=payload.logo_key))
