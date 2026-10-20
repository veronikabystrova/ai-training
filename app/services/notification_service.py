"""Emails sent by background jobs.

Every method re-reads current state by id and skips when it no longer applies (token used or
expired, approval decided, account gone), then sends at most once per idempotency key so an
ARQ retry does not double a mail, except when a job times out after the provider accepted it
(no provider idempotency key yet; accepted risk, spec decision 21). A failed send releases its
claim so the retry can send.

Raw one-time tokens arrive only as job kwargs; the idempotency key uses their hash.
"""

from uuid import UUID

import structlog

from app.core.clock import Clock
from app.core.config import (
    CONFIRM_EMAIL_CHANGE_ROUTE,
    RESET_ROUTE,
    SETUP_ROUTE,
    VERIFY_EMAIL_ROUTE,
    Settings,
)
from app.core.email import EmailSender
from app.core.once import OnceStore
from app.core.security import hash_token
from app.db.session import SessionFactory
from app.models.identity import ApprovalStatus, OttPurpose, User, UserStatus
from app.repositories.approval import PurchaseApprovalRepository
from app.repositories.one_time_token import OneTimeTokenRepository
from app.repositories.player import PlayerProfileRepository
from app.repositories.user import UserRepository

_TOKEN_PATHS = {
    OttPurpose.trainer_setup: SETUP_ROUTE,
    OttPurpose.email_verify: VERIFY_EMAIL_ROUTE,
    OttPurpose.password_reset: RESET_ROUTE,
    OttPurpose.email_change: CONFIRM_EMAIL_CHANGE_ROUTE,
}


class NotificationService:
    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        clock: Clock,
        settings: Settings,
        email: EmailSender,
        once: OnceStore,
    ) -> None:
        self._session_factory = session_factory
        self._clock = clock
        self._settings = settings
        self._email = email
        self._once = once
        self._log = structlog.get_logger(__name__)

    # --- token emails --------------------------------------------------------------------

    async def send_trainer_invite(self, *, user_id: UUID, token: str) -> bool:
        user = await self._live_token_user(user_id, token, OttPurpose.trainer_setup)
        if user is None or user.status is not UserStatus.pending:
            return self._skip("send_trainer_invite")
        return await self._send_once(
            self._token_key("trainer_invite", token),
            to=user.email,
            template="trainer_invite",
            context=self._token_context(OttPurpose.trainer_setup, token),
        )

    async def send_verification_email(self, *, user_id: UUID, token: str) -> bool:
        user = await self._live_token_user(user_id, token, OttPurpose.email_verify)
        if user is None or user.is_email_verified:
            return self._skip("send_verification_email")
        return await self._send_once(
            self._token_key("verification", token),
            to=user.email,
            template="email_verification",
            context=self._token_context(OttPurpose.email_verify, token),
        )

    async def send_password_reset(self, *, user_id: UUID, token: str) -> bool:
        user = await self._live_token_user(user_id, token, OttPurpose.password_reset)
        if user is None:
            return self._skip("send_password_reset")
        return await self._send_once(
            self._token_key("password_reset", token),
            to=user.email,
            template="password_reset",
            context=self._token_context(OttPurpose.password_reset, token),
        )

    async def send_email_change(self, *, user_id: UUID, token: str, new_email: str) -> bool:
        """The confirmation goes to the NEW address, proving the user controls it."""
        user = await self._live_token_user(user_id, token, OttPurpose.email_change)
        if user is None:
            return self._skip("send_email_change")
        return await self._send_once(
            self._token_key("email_change", token),
            to=new_email,
            template="email_change",
            context=self._token_context(OttPurpose.email_change, token),
        )

    # --- other emails --------------------------------------------------------------------

    async def send_welcome_email(self, *, user_id: UUID) -> bool:
        async with self._session_factory() as session:
            user = await UserRepository(session).get(user_id)
        if user is None or user.status is UserStatus.deleted:
            return self._skip("send_welcome_email")
        return await self._send_once(
            f"welcome:{user_id}", to=user.email, template="welcome", context={}
        )

    async def notify_parent_approval_request(self, *, approval_id: UUID) -> bool:
        async with self._session_factory() as session:
            approval = await PurchaseApprovalRepository(session).get_for_notification(approval_id)
            parent = (
                None
                if approval is None
                else await UserRepository(session).get(approval.parent_user_id)
            )
        if (
            approval is None
            or parent is None
            or approval.status is not ApprovalStatus.pending
            or parent.status in (UserStatus.deleted, UserStatus.inactive)
        ):
            return self._skip("notify_parent_approval_request")
        return await self._send_once(
            f"approval:{approval_id}:requested",
            to=parent.email,
            template="parent_approval_request",
            context={"approval_id": str(approval_id)},
        )

    async def notify_child_decision(self, *, approval_id: UUID) -> bool:
        """Tell the child's login the parent's decision; skipped while pending or loginless."""
        async with self._session_factory() as session:
            approval = await PurchaseApprovalRepository(session).get_for_notification(approval_id)
            if approval is None or approval.status is ApprovalStatus.pending:
                return self._skip("notify_child_decision")
            profile = await PlayerProfileRepository(session).get(approval.player_profile_id)
            child = (
                None
                if profile is None or profile.login_user_id is None
                else await UserRepository(session).get(profile.login_user_id)
            )
        if child is None or child.status is UserStatus.deleted:
            return self._skip("notify_child_decision")
        return await self._send_once(
            f"approval:{approval_id}:{approval.status.value}",
            to=child.email,
            template="child_approval_decision",
            context={"approval_id": str(approval_id), "status": approval.status.value},
        )

    async def notify_parent_share_link_blocked(
        self, *, player_profile_id: UUID, share_link_id: UUID
    ) -> bool:
        async with self._session_factory() as session:
            profile = await PlayerProfileRepository(session).get(player_profile_id)
            parent = (
                None
                if profile is None
                else await UserRepository(session).get(profile.owner_user_id)
            )
        if parent is None or parent.status is UserStatus.deleted:
            return self._skip("notify_parent_share_link_blocked")
        return await self._send_once(
            f"share-link-blocked:{player_profile_id}:{share_link_id}",
            to=parent.email,
            template="parent_share_link_blocked",
            context={"player_profile_id": str(player_profile_id)},
        )

    # --- helpers -------------------------------------------------------------------------

    async def _live_token_user(self, user_id: UUID, token: str, purpose: OttPurpose) -> User | None:
        """The user, only while the token is unused, unexpired and belongs to that user."""
        async with self._session_factory() as session:
            owner = await OneTimeTokenRepository(session).live_owner(
                raw=token, purpose=purpose, now=self._clock.now()
            )
            if owner is None or owner != user_id:
                return None
            user = await UserRepository(session).get(user_id)
        return None if user is None or user.status is UserStatus.deleted else user

    @staticmethod
    def _token_key(kind: str, token: str) -> str:
        return f"{kind}:{hash_token(token).hex()}"

    def _token_context(self, purpose: OttPurpose, token: str) -> dict[str, str]:
        return {"link": self._settings.frontend_url(_TOKEN_PATHS[purpose], token=token)}

    def _skip(self, job: str) -> bool:
        self._log.info("job.skipped", job=job, reason="state_changed")
        return False

    async def _send_once(
        self, key: str, *, to: str, template: str, context: dict[str, str]
    ) -> bool:
        if not await self._once.claim(key):
            self._log.info("job.skipped", job=template, reason="already_sent")
            return False
        try:
            await self._email.send(to=to, template=template, context=context)
        except BaseException:
            await self._once.release(key)
            raise
        return True
