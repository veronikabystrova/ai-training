"""Dependents under ``/me/players`` (tag ``me-players``; player role, never a child login)."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.deps import Principal, SessionDep
from app.api.v1.me.deps import ERRORS, by_principal, require_parent
from app.api.v1.me.schemas import AvailabilitySet, availability_response
from app.services.availability_service import AvailabilityService

router = APIRouter(prefix="/me/players", tags=["me-players"])

ParentDep = Annotated[Principal, Depends(require_parent)]


def get_availability_service(session: SessionDep) -> AvailabilityService:
    return AvailabilityService(session=session)


AvailabilityServiceDep = Annotated[AvailabilityService, Depends(get_availability_service)]


@router.put(
    "/{player_id}/availability",
    response_model=AvailabilitySet,
    summary="Replace a dependent's weekly availability",
    dependencies=[by_principal],
    responses={
        **ERRORS,
        403: {"description": "Not a parent account"},
        404: {"description": "Not the caller's profile"},
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
