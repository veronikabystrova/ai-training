"""Parent approvals under ``/me/approvals`` (tag ``me-approvals``; parent accounts only).

Reads are tenant-bound (``?trainer_id`` is required). Decisions need an ``Idempotency-Key``.
"""

from uuid import UUID

from fastapi import APIRouter

from app.api.deps import IdempotencyDep, PlayerContextDep
from app.api.v1.me.deps import ERRORS, ApprovalServiceDep, ParentDep, by_principal
from app.api.v1.me.schemas import ApprovalResponse, DenyRequest, approval_response
from app.core.pagination import CursorQ, LimitQ, Page
from app.models.identity import ApprovalStatus
from app.services.approval_service import Decision, DecisionResult

router = APIRouter(prefix="/me/approvals", tags=["me-approvals"])

_DECISION_ERRORS = {
    **ERRORS,
    400: {"description": "IDEMPOTENCY_KEY_REQUIRED"},
    403: {"description": "Not a parent account"},
    404: {"description": "Not the caller's approval"},
    409: {"description": "APPROVAL_NOT_PENDING or IDEMPOTENCY_KEY_REUSED"},
}


def _respond(result: DecisionResult) -> ApprovalResponse:
    # Fresh and replayed decisions both come from the stored JSON, so they are identical.
    return ApprovalResponse.model_validate(result.body)


@router.get(
    "",
    response_model=Page[ApprovalResponse],
    summary="The caller's dependents' approvals under one trainer",
    dependencies=[by_principal],
    responses={
        401: ERRORS[401],
        403: {"description": "Not a parent account"},
        404: {"description": "trainer_id is not a valid context"},
        422: ERRORS[422],
    },
)
async def list_approvals(
    principal: ParentDep,
    scope: PlayerContextDep,
    service: ApprovalServiceDep,
    status: ApprovalStatus | None = None,
    limit: LimitQ = 50,
    cursor: CursorQ = None,
) -> Page[ApprovalResponse]:
    views, next_cursor = await service.list_approvals(
        parent_user_id=principal.user_id,
        trainer_id=scope.trainer_id,
        status=status,
        limit=limit,
        cursor=cursor,
    )
    return Page(items=[approval_response(v) for v in views], next_cursor=next_cursor)


@router.post(
    "/{approval_id}/approve",
    response_model=ApprovalResponse,
    summary="Approve a purchase (executes it once)",
    dependencies=[by_principal],
    responses=_DECISION_ERRORS,
)
async def approve(
    approval_id: UUID,
    principal: ParentDep,
    guard: IdempotencyDep,
    service: ApprovalServiceDep,
) -> ApprovalResponse:
    result = await service.decide(
        parent_user_id=principal.user_id,
        approval_id=approval_id,
        decision=Decision.approve,
        note=None,
        guard=guard,
    )
    return _respond(result)


@router.post(
    "/{approval_id}/deny",
    response_model=ApprovalResponse,
    summary="Deny a purchase",
    dependencies=[by_principal],
    responses=_DECISION_ERRORS,
)
async def deny(
    approval_id: UUID,
    principal: ParentDep,
    guard: IdempotencyDep,
    service: ApprovalServiceDep,
    payload: DenyRequest | None = None,
) -> ApprovalResponse:
    result = await service.decide(
        parent_user_id=principal.user_id,
        approval_id=approval_id,
        decision=Decision.deny,
        note=payload.note if payload else None,
        guard=guard,
    )
    return _respond(result)
