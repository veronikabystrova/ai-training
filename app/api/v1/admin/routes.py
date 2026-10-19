from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response

from app.api.deps import FingerprintDep, SuperAdminScope
from app.api.v1.admin.deps import ERRORS, AdminUserServiceDep, by_principal
from app.api.v1.admin.schemas import (
    AdminUpdateUser,
    AdminUserDetail,
    AdminUserSummary,
    ImpersonationResponse,
    ProvisionTrainer,
    detail_response,
    impersonation_response,
    summary_response,
)
from app.api.v1.auth.deps import ImpersonationServiceDep
from app.core.pagination import CursorQ, LimitQ, Page
from app.models.identity import UserRole, UserStatus

router = APIRouter(prefix="/admin", tags=["admin"])

_NOT_FOUND = {"description": "Unknown user"}
_ERASED = {"description": "USER_ERASED"}


@router.get(
    "/users",
    response_model=Page[AdminUserSummary],
    summary="User directory",
    dependencies=[by_principal],
    responses=ERRORS,
)
async def list_users(
    _: SuperAdminScope,
    service: AdminUserServiceDep,
    role: UserRole | None = None,
    status: UserStatus | None = None,
    trainer_id: UUID | None = None,
    query: Annotated[str | None, Query(min_length=2, max_length=100)] = None,
    limit: LimitQ = 50,
    cursor: CursorQ = None,
) -> Page[AdminUserSummary]:
    views, next_cursor = await service.list_users(
        query=query,
        role=role,
        status=status,
        trainer_id=trainer_id,
        cursor=cursor,
        limit=limit,
    )
    return Page[AdminUserSummary](
        items=[summary_response(v) for v in views], next_cursor=next_cursor
    )


@router.post(
    "/users/trainers",
    status_code=201,
    response_model=AdminUserDetail,
    summary="Provision a trainer",
    dependencies=[by_principal],
    responses={**ERRORS, 409: {"description": "EMAIL_TAKEN or SLUG_TAKEN"}},
)
async def provision_trainer(
    payload: ProvisionTrainer, admin: SuperAdminScope, service: AdminUserServiceDep
) -> AdminUserDetail:
    view = await service.provision_trainer(admin_id=admin.user_id, data=payload.to_input())
    return detail_response(view)


@router.get(
    "/users/{user_id}",
    response_model=AdminUserDetail,
    summary="One user",
    dependencies=[by_principal],
    responses={**ERRORS, 404: _NOT_FOUND},
)
async def get_user(
    user_id: UUID, _: SuperAdminScope, service: AdminUserServiceDep
) -> AdminUserDetail:
    return detail_response(await service.get_user(user_id))


@router.patch(
    "/users/{user_id}",
    response_model=AdminUserDetail,
    summary="Edit a user's profile",
    dependencies=[by_principal],
    responses={**ERRORS, 404: _NOT_FOUND, 409: _ERASED},
)
async def update_user(
    user_id: UUID,
    payload: AdminUpdateUser,
    admin: SuperAdminScope,
    service: AdminUserServiceDep,
) -> AdminUserDetail:
    view = await service.update_user(
        admin_id=admin.user_id, user_id=user_id, changes=payload.changes()
    )
    return detail_response(view)


@router.post(
    "/users/{user_id}/deactivate",
    response_model=AdminUserDetail,
    summary="Deactivate a user",
    dependencies=[by_principal],
    responses={
        **ERRORS,
        404: _NOT_FOUND,
        409: {"description": "USER_ERASED or INVALID_STATUS_TRANSITION (pending)"},
    },
)
async def deactivate_user(
    user_id: UUID, admin: SuperAdminScope, service: AdminUserServiceDep
) -> AdminUserDetail:
    return detail_response(await service.deactivate(admin_id=admin.user_id, user_id=user_id))


@router.post(
    "/users/{user_id}/reactivate",
    response_model=AdminUserDetail,
    summary="Reactivate a user",
    dependencies=[by_principal],
    responses={
        **ERRORS,
        404: _NOT_FOUND,
        409: {"description": "USER_ERASED or INVALID_STATUS_TRANSITION (pending)"},
    },
)
async def reactivate_user(
    user_id: UUID, admin: SuperAdminScope, service: AdminUserServiceDep
) -> AdminUserDetail:
    return detail_response(await service.reactivate(admin_id=admin.user_id, user_id=user_id))


@router.delete(
    "/users/{user_id}",
    status_code=204,
    summary="Erase a user (GDPR)",
    dependencies=[by_principal],
    responses={**ERRORS, 404: _NOT_FOUND, 409: {"description": "ALREADY_ERASED"}},
)
async def erase_user(
    user_id: UUID, admin: SuperAdminScope, service: AdminUserServiceDep
) -> Response:
    await service.erase(admin_id=admin.user_id, user_id=user_id)
    return Response(status_code=204)


@router.post(
    "/users/{user_id}/impersonate",
    response_model=ImpersonationResponse,
    summary="Start impersonating a user",
    dependencies=[by_principal],
    responses={
        **ERRORS,
        403: {"description": "Not a super admin, or CANNOT_IMPERSONATE_ADMIN"},
        404: _NOT_FOUND,
        409: {"description": "USER_NOT_ACTIVE"},
    },
)
async def impersonate_user(
    user_id: UUID,
    admin: SuperAdminScope,
    service: ImpersonationServiceDep,
    client: FingerprintDep,
) -> ImpersonationResponse:
    started = await service.start(admin_id=admin.user_id, target_id=user_id, client=client)
    return impersonation_response(started)
