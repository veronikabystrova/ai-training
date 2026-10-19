"""ARQ job functions for Epic-01. Thin: they unpack kwargs and call a service.

``ctx`` is the worker context built by ``app.jobs.worker.startup`` and holds the two services.
Job names must equal the names services enqueue (``app.core.jobs.JobQueue.enqueue``).
"""

from typing import Any
from uuid import UUID

from app.services.maintenance_service import MaintenanceService
from app.services.notification_service import NotificationService

Ctx = dict[str, Any]


def _notifications(ctx: Ctx) -> NotificationService:
    service: NotificationService = ctx["notifications"]
    return service


def _maintenance(ctx: Ctx) -> MaintenanceService:
    service: MaintenanceService = ctx["maintenance"]
    return service


async def send_trainer_invite(ctx: Ctx, *, user_id: str, token: str) -> None:
    await _notifications(ctx).send_trainer_invite(user_id=UUID(user_id), token=token)


async def send_welcome_email(ctx: Ctx, *, user_id: str) -> None:
    await _notifications(ctx).send_welcome_email(user_id=UUID(user_id))


async def send_verification_email(ctx: Ctx, *, user_id: str, token: str) -> None:
    await _notifications(ctx).send_verification_email(user_id=UUID(user_id), token=token)


async def send_password_reset(ctx: Ctx, *, user_id: str, token: str) -> None:
    await _notifications(ctx).send_password_reset(user_id=UUID(user_id), token=token)


async def send_email_change(ctx: Ctx, *, user_id: str, token: str, new_email: str) -> None:
    await _notifications(ctx).send_email_change(
        user_id=UUID(user_id), token=token, new_email=new_email
    )


async def notify_parent_approval_request(ctx: Ctx, *, approval_id: str) -> None:
    await _notifications(ctx).notify_parent_approval_request(approval_id=UUID(approval_id))


async def notify_child_approval_denied(ctx: Ctx, *, approval_id: str) -> None:
    """Enqueued by ``ApprovalService`` on deny; the service handles any decided status."""
    await _notifications(ctx).notify_child_decision(approval_id=UUID(approval_id))


async def notify_parent_share_link_blocked(
    ctx: Ctx, *, player_profile_id: str, share_link_id: str
) -> None:
    await _notifications(ctx).notify_parent_share_link_blocked(
        player_profile_id=UUID(player_profile_id), share_link_id=UUID(share_link_id)
    )


async def expire_purchase_approvals(ctx: Ctx) -> None:
    await _maintenance(ctx).expire_purchase_approvals()


async def expire_impersonation_sessions(ctx: Ctx) -> None:
    await _maintenance(ctx).expire_impersonation_sessions()


async def purge_expired_tokens(ctx: Ctx) -> None:
    await _maintenance(ctx).purge_expired_tokens()
