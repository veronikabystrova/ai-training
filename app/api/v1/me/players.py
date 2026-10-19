"""Dependents under ``/me/players`` (tag ``me-players``).

Reads are open to a child login (its own profile only); every write needs a parent account.
"""

from uuid import UUID

from fastapi import APIRouter, Response

from app.api.v1.me.deps import (
    ERRORS,
    AvailabilityServiceDep,
    DependentCreatorDep,
    ParentDep,
    PlayerCallerDep,
    PlayerServiceDep,
    by_principal,
)
from app.api.v1.me.schemas import (
    AddTrainerLink,
    AvailabilitySet,
    CreatePlayer,
    PlayerResponse,
    TrainerLinkResponse,
    UpdatePlayer,
    availability_response,
    player_response,
    trainer_link_response,
)

router = APIRouter(prefix="/me/players", tags=["me-players"])

_PARENT_ONLY = {"description": "Not a parent account"}
_NOT_OWNER = {"description": "Not the caller's profile"}


@router.get(
    "",
    response_model=list[PlayerResponse],
    summary="Own profile and dependents",
    dependencies=[by_principal],
    responses={401: ERRORS[401], 403: {"description": "Not a player"}},
)
async def list_players(
    principal: PlayerCallerDep, service: PlayerServiceDep
) -> list[PlayerResponse]:
    views = await service.list_players(user_id=principal.user_id, child=principal.child)
    return [player_response(v) for v in views]


@router.post(
    "",
    status_code=201,
    response_model=PlayerResponse,
    summary="Create a dependent",
    dependencies=[by_principal],
    responses={
        **ERRORS,
        403: {"description": "CHILD_CANNOT_CREATE_DEPENDENT or FORBIDDEN"},
        404: {"description": "A trainer_ids entry is not linked to the parent"},
        409: {"description": "DUPLICATE_PLAYER or EMAIL_TAKEN"},
        422: {"description": "INVALID_AGE, WEAK_PASSWORD or VALIDATION_ERROR"},
    },
)
async def create_player(
    payload: CreatePlayer,
    principal: DependentCreatorDep,
    service: PlayerServiceDep,
    allow_duplicate: bool = False,
) -> PlayerResponse:
    view = await service.create_player(
        owner_user_id=principal.user_id, data=payload.to_input(), allow_duplicate=allow_duplicate
    )
    return player_response(view)


@router.get(
    "/{player_id}",
    response_model=PlayerResponse,
    summary="One profile",
    dependencies=[by_principal],
    responses={401: ERRORS[401], 403: {"description": "Not a player"}, 404: _NOT_OWNER},
)
async def get_player(
    player_id: UUID, principal: PlayerCallerDep, service: PlayerServiceDep
) -> PlayerResponse:
    view = await service.get_player(
        user_id=principal.user_id, child=principal.child, player_id=player_id
    )
    return player_response(view)


@router.patch(
    "/{player_id}",
    response_model=PlayerResponse,
    summary="Update a dependent",
    dependencies=[by_principal],
    responses={
        **ERRORS,
        403: _PARENT_ONLY,
        404: _NOT_OWNER,
        409: {"description": "EMAIL_TAKEN"},
        422: {"description": "INVALID_AGE, WEAK_PASSWORD or VALIDATION_ERROR"},
    },
)
async def update_player(
    player_id: UUID, payload: UpdatePlayer, principal: ParentDep, service: PlayerServiceDep
) -> PlayerResponse:
    view = await service.update_player(
        owner_user_id=principal.user_id, player_id=player_id, changes=payload.changes()
    )
    return player_response(view)


@router.delete(
    "/{player_id}",
    status_code=204,
    summary="Remove a dependent (soft)",
    dependencies=[by_principal],
    responses={
        401: ERRORS[401],
        403: _PARENT_ONLY,
        404: _NOT_OWNER,
        409: {"description": "ACTIVE_COMMITMENTS or CANNOT_REMOVE_OWN_PROFILE"},
        429: ERRORS[429],
    },
)
async def remove_player(
    player_id: UUID, principal: ParentDep, service: PlayerServiceDep
) -> Response:
    await service.remove_player(owner_user_id=principal.user_id, player_id=player_id)
    return Response(status_code=204)


@router.put(
    "/{player_id}/availability",
    response_model=AvailabilitySet,
    summary="Replace a dependent's weekly availability",
    dependencies=[by_principal],
    responses={
        **ERRORS,
        403: _PARENT_ONLY,
        404: _NOT_OWNER,
        422: {"description": "OVERLAPPING_WINDOWS or VALIDATION_ERROR"},
    },
)
async def put_player_availability(
    player_id: UUID,
    payload: AvailabilitySet,
    principal: ParentDep,
    service: AvailabilityServiceDep,
) -> AvailabilitySet:
    view = await service.replace_for_dependent(
        owner_user_id=principal.user_id,
        player_profile_id=player_id,
        timezone=payload.timezone,
        windows=payload.specs(),
    )
    return availability_response(view)


@router.post(
    "/{player_id}/trainers",
    response_model=TrainerLinkResponse,
    summary="Link a dependent to a trainer",
    dependencies=[by_principal],
    responses={
        **ERRORS,
        200: {"description": "Already linked and active", "model": TrainerLinkResponse},
        201: {"description": "Linked or reactivated", "model": TrainerLinkResponse},
        403: _PARENT_ONLY,
        404: {"description": "Not the caller's profile, or the trainer is not one of theirs"},
    },
)
async def add_trainer(
    player_id: UUID,
    payload: AddTrainerLink,
    response: Response,
    principal: ParentDep,
    service: PlayerServiceDep,
) -> TrainerLinkResponse:
    link, changed = await service.add_trainer(
        owner_user_id=principal.user_id, player_id=player_id, trainer_id=payload.trainer_id
    )
    response.status_code = 201 if changed else 200
    return trainer_link_response(link)


@router.delete(
    "/{player_id}/trainers/{trainer_id}",
    status_code=204,
    summary="Unlink a dependent from a trainer (soft)",
    dependencies=[by_principal],
    responses={
        401: ERRORS[401],
        403: _PARENT_ONLY,
        404: {"description": "Not the caller's profile or no active link"},
        409: {"description": "ACTIVE_COMMITMENTS"},
        429: ERRORS[429],
    },
)
async def remove_trainer(
    player_id: UUID, trainer_id: UUID, principal: ParentDep, service: PlayerServiceDep
) -> Response:
    await service.remove_trainer(
        owner_user_id=principal.user_id, player_id=player_id, trainer_id=trainer_id
    )
    return Response(status_code=204)
