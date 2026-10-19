from uuid import UUID

from fastapi import APIRouter, Response

from app.api.deps import SettingsDep
from app.api.v1.trainer.deps import (
    ERRORS,
    AvailabilityServiceDep,
    ShareLinkServiceDep,
    StaffScopeDep,
    TrainerScopeDep,
    by_principal,
)
from app.api.v1.trainer.schemas import (
    AvailabilityRead,
    CreateShareLink,
    ShareLinkResponse,
    availability_read,
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
