from collections.abc import Awaitable, Callable
from datetime import timedelta
from uuid import UUID

import pytest
import structlog.testing
from arq.worker import Retry
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import new_id
from app.jobs import identity_jobs
from app.jobs.identity_jobs import Ctx
from app.jobs.worker import MAX_TRIES, WorkerSettings, with_retries
from app.models.identity import (
    ApprovalStatus,
    ImpersonationSession,
    OneTimeToken,
    OttPurpose,
    PurchaseApproval,
    UserStatus,
)
from app.repositories.one_time_token import OneTimeTokenRepository
from tests.factories import LinkPlayer, MakeApproval, MakeDependent, MakeTrainer, MakeUser
from tests.fakes import FakeClock, FakeEmailSender

IssueToken = Callable[..., Awaitable[str]]


@pytest.fixture
def issue_token(db_session: AsyncSession, clock: FakeClock) -> IssueToken:
    async def _issue(
        user_id: UUID, purpose: OttPurpose, *, ttl: timedelta = timedelta(hours=1)
    ) -> str:
        return await OneTimeTokenRepository(db_session).issue(
            user_id=user_id, purpose=purpose, ttl=ttl, now=clock.now()
        )

    return _issue


# --- token emails ---------------------------------------------------------------------------


async def test_send_password_reset_sends_once_even_if_retried(
    ctx: Ctx, make_user: MakeUser, issue_token: IssueToken, email: FakeEmailSender
) -> None:
    user = await make_user()
    token = await issue_token(user.id, OttPurpose.password_reset)
    await identity_jobs.send_password_reset(ctx, user_id=str(user.id), token=token)
    await identity_jobs.send_password_reset(ctx, user_id=str(user.id), token=token)
    assert len(email.sent) == 1
    sent = email.sent[0]
    assert (sent.to, sent.template) == (user.email, "password_reset")
    assert sent.context["link"].endswith(f"/reset-password?token={token}")


async def test_job_skips_when_token_already_used(
    ctx: Ctx,
    db_session: AsyncSession,
    clock: FakeClock,
    make_user: MakeUser,
    issue_token: IssueToken,
    email: FakeEmailSender,
) -> None:
    user = await make_user()
    token = await issue_token(user.id, OttPurpose.password_reset)
    await OneTimeTokenRepository(db_session).consume(
        raw=token, purpose=OttPurpose.password_reset, now=clock.now()
    )
    await identity_jobs.send_password_reset(ctx, user_id=str(user.id), token=token)
    assert email.sent == []


async def test_job_skips_when_token_expired_or_belongs_to_another_user(
    ctx: Ctx,
    clock: FakeClock,
    make_user: MakeUser,
    issue_token: IssueToken,
    email: FakeEmailSender,
) -> None:
    owner, other = await make_user(), await make_user()
    token = await issue_token(owner.id, OttPurpose.password_reset)
    await identity_jobs.send_password_reset(ctx, user_id=str(other.id), token=token)
    clock.advance(hours=1)
    await identity_jobs.send_password_reset(ctx, user_id=str(owner.id), token=token)
    assert email.sent == []


async def test_failed_send_releases_the_claim_so_the_retry_sends(
    ctx: Ctx,
    make_user: MakeUser,
    issue_token: IssueToken,
    email: FakeEmailSender,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await make_user()
    token = await issue_token(user.id, OttPurpose.password_reset)
    real_send = email.send

    async def boom(**_: object) -> None:
        raise ConnectionError("smtp down")

    monkeypatch.setattr(email, "send", boom)
    with pytest.raises(ConnectionError):
        await identity_jobs.send_password_reset(ctx, user_id=str(user.id), token=token)
    monkeypatch.setattr(email, "send", real_send)
    await identity_jobs.send_password_reset(ctx, user_id=str(user.id), token=token)
    assert len(email.sent) == 1


async def test_send_trainer_invite_only_for_pending_trainer(
    ctx: Ctx, make_trainer: MakeTrainer, issue_token: IssueToken, email: FakeEmailSender
) -> None:
    trainer = await make_trainer(status=UserStatus.pending)
    token = await issue_token(trainer.id, OttPurpose.trainer_setup, ttl=timedelta(hours=24))
    await identity_jobs.send_trainer_invite(ctx, user_id=str(trainer.id), token=token)
    assert [(e.to, e.template) for e in email.sent] == [(trainer.email, "trainer_invite")]
    assert email.sent[0].context["link"].endswith(f"/setup-password?token={token}")


async def test_send_trainer_invite_skips_when_already_active(
    ctx: Ctx, make_trainer: MakeTrainer, issue_token: IssueToken, email: FakeEmailSender
) -> None:
    trainer = await make_trainer()  # active: the setup already happened
    token = await issue_token(trainer.id, OttPurpose.trainer_setup)
    await identity_jobs.send_trainer_invite(ctx, user_id=str(trainer.id), token=token)
    assert email.sent == []


async def test_send_verification_email_skips_when_already_verified(
    ctx: Ctx, make_user: MakeUser, issue_token: IssueToken, email: FakeEmailSender
) -> None:
    verified = await make_user()  # factories create verified users
    token = await issue_token(verified.id, OttPurpose.email_verify)
    await identity_jobs.send_verification_email(ctx, user_id=str(verified.id), token=token)
    assert email.sent == []
    verified.is_email_verified = False
    await identity_jobs.send_verification_email(ctx, user_id=str(verified.id), token=token)
    assert [e.template for e in email.sent] == ["email_verification"]


async def test_send_email_change_goes_to_the_new_address(
    ctx: Ctx, make_user: MakeUser, issue_token: IssueToken, email: FakeEmailSender
) -> None:
    user = await make_user()
    token = await issue_token(user.id, OttPurpose.email_change, ttl=timedelta(hours=24))
    await identity_jobs.send_email_change(
        ctx, user_id=str(user.id), token=token, new_email="new@example.com"
    )
    assert [(e.to, e.template) for e in email.sent] == [("new@example.com", "email_change")]


async def test_send_welcome_email_once_and_skips_deleted(
    ctx: Ctx, make_user: MakeUser, email: FakeEmailSender
) -> None:
    user = await make_user()
    await identity_jobs.send_welcome_email(ctx, user_id=str(user.id))
    await identity_jobs.send_welcome_email(ctx, user_id=str(user.id))
    gone = await make_user(status=UserStatus.deleted)
    await identity_jobs.send_welcome_email(ctx, user_id=str(gone.id))
    assert [(e.to, e.template) for e in email.sent] == [(user.email, "welcome")]


# --- approval and share-link notifications ------------------------------------------------


async def test_notify_parent_approval_request_only_while_pending(
    ctx: Ctx,
    make_user: MakeUser,
    make_trainer: MakeTrainer,
    make_dependent: MakeDependent,
    link_player: LinkPlayer,
    make_approval: MakeApproval,
    email: FakeEmailSender,
) -> None:
    parent, trainer = await make_user(), await make_trainer()
    child = await make_dependent(parent=parent)
    await link_player(trainer, child)
    approval = await make_approval(parent=parent, trainer=trainer, player=child)
    await identity_jobs.notify_parent_approval_request(ctx, approval_id=str(approval.id))
    await identity_jobs.notify_parent_approval_request(ctx, approval_id=str(approval.id))
    assert [(e.to, e.template) for e in email.sent] == [(parent.email, "parent_approval_request")]

    decided = await make_approval(parent=parent, trainer=trainer, player=child)
    decided.status = ApprovalStatus.approved
    await identity_jobs.notify_parent_approval_request(ctx, approval_id=str(decided.id))
    assert len(email.sent) == 1


async def test_notify_child_decision_tells_the_child_login_once_per_status(
    ctx: Ctx,
    make_user: MakeUser,
    make_trainer: MakeTrainer,
    make_dependent: MakeDependent,
    link_player: LinkPlayer,
    make_approval: MakeApproval,
    email: FakeEmailSender,
) -> None:
    parent, trainer = await make_user(), await make_trainer()
    child = await make_dependent(parent=parent, with_login=True)
    await link_player(trainer, child)
    approval = await make_approval(parent=parent, trainer=trainer, player=child)
    job = identity_jobs.notify_child_approval_denied

    await job(ctx, approval_id=str(approval.id))  # still pending: nothing to report
    assert email.sent == []
    approval.status = ApprovalStatus.denied
    await job(ctx, approval_id=str(approval.id))
    await job(ctx, approval_id=str(approval.id))
    assert len(email.sent) == 1
    assert email.sent[0].context["status"] == "denied"
    assert child.login_user_id is not None


async def test_notify_child_decision_skips_dependent_without_login(
    ctx: Ctx,
    make_user: MakeUser,
    make_trainer: MakeTrainer,
    make_dependent: MakeDependent,
    link_player: LinkPlayer,
    make_approval: MakeApproval,
    email: FakeEmailSender,
) -> None:
    parent, trainer = await make_user(), await make_trainer()
    child = await make_dependent(parent=parent)
    await link_player(trainer, child)
    approval = await make_approval(parent=parent, trainer=trainer, player=child)
    approval.status = ApprovalStatus.denied
    await identity_jobs.notify_child_approval_denied(ctx, approval_id=str(approval.id))
    assert email.sent == []


async def test_notify_parent_share_link_blocked_emails_the_owner_once(
    ctx: Ctx, make_user: MakeUser, make_dependent: MakeDependent, email: FakeEmailSender
) -> None:
    parent = await make_user()
    child = await make_dependent(parent=parent, with_login=True)
    link_id = str(new_id())
    for _ in range(2):
        await identity_jobs.notify_parent_share_link_blocked(
            ctx, player_profile_id=str(child.id), share_link_id=link_id
        )
    assert [(e.to, e.template) for e in email.sent] == [(parent.email, "parent_share_link_blocked")]


# --- cron sweeps ----------------------------------------------------------------------------


async def test_expire_purchase_approvals_marks_pending_past_expiry(
    ctx: Ctx,
    db_session: AsyncSession,
    clock: FakeClock,
    make_user: MakeUser,
    make_trainer: MakeTrainer,
    make_dependent: MakeDependent,
    link_player: LinkPlayer,
    make_approval: MakeApproval,
) -> None:
    parent, trainer = await make_user(), await make_trainer()
    child = await make_dependent(parent=parent)
    await link_player(trainer, child)
    due = await make_approval(parent=parent, trainer=trainer, player=child)
    later = await make_approval(
        parent=parent, trainer=trainer, player=child, expires_in=timedelta(hours=60)
    )
    clock.advance(hours=48)

    await identity_jobs.expire_purchase_approvals(ctx)
    await identity_jobs.expire_purchase_approvals(ctx)  # per-row guard: second run is a no-op

    rows = {
        a.id: a
        for a in (
            await db_session.execute(
                select(PurchaseApproval)
                .where(PurchaseApproval.id.in_([due.id, later.id]))
                .execution_options(populate_existing=True)
            )
        ).scalars()
    }
    assert rows[due.id].status is ApprovalStatus.expired
    assert rows[due.id].responded_at == clock.now()
    assert rows[later.id].status is ApprovalStatus.pending


async def test_expire_impersonation_sessions_closes_expired(
    ctx: Ctx, db_session: AsyncSession, clock: FakeClock, make_user: MakeUser
) -> None:
    admin, target = await make_user(), await make_user()
    expired = ImpersonationSession(
        admin_user_id=admin.id,
        target_user_id=target.id,
        jti=new_id(),
        expires_at=clock.now() + timedelta(minutes=60),
    )
    live = ImpersonationSession(
        admin_user_id=admin.id,
        target_user_id=target.id,
        jti=new_id(),
        expires_at=clock.now() + timedelta(minutes=90),
    )
    db_session.add_all([expired, live])
    await db_session.flush()
    clock.advance(minutes=60)

    await identity_jobs.expire_impersonation_sessions(ctx)

    await db_session.refresh(expired)
    await db_session.refresh(live)
    assert expired.ended_at == clock.now()
    assert live.ended_at is None


async def test_purge_expired_tokens_deletes_expired_and_used_over_7_days(
    ctx: Ctx,
    db_session: AsyncSession,
    clock: FakeClock,
    make_user: MakeUser,
    issue_token: IssueToken,
) -> None:
    user = await make_user()
    repo = OneTimeTokenRepository(db_session)
    await issue_token(user.id, OttPurpose.email_verify, ttl=timedelta(hours=1))
    used_old = await issue_token(user.id, OttPurpose.password_reset, ttl=timedelta(days=30))
    await repo.consume(raw=used_old, purpose=OttPurpose.password_reset, now=clock.now())
    used_recent = await issue_token(user.id, OttPurpose.email_change, ttl=timedelta(days=30))
    await issue_token(user.id, OttPurpose.trainer_setup, ttl=timedelta(days=30))
    clock.advance(days=8)
    await repo.consume(raw=used_recent, purpose=OttPurpose.email_change, now=clock.now())

    await identity_jobs.purge_expired_tokens(ctx)

    remaining = set((await db_session.execute(select(OneTimeToken.purpose))).scalars())
    assert remaining == {OttPurpose.email_change, OttPurpose.trainer_setup}


# --- worker settings, retry and dead letter -------------------------------------------------


async def test_worker_settings_retry_5_attempts_exponential_and_cron_registry() -> None:
    s = WorkerSettings
    assert s.max_tries == 5
    assert s.retry_jobs is True
    assert {c.name for c in s.cron_jobs} >= {
        "expire_purchase_approvals",
        "expire_impersonation_sessions",
        "purge_expired_tokens",
    }
    crons = {c.name: c for c in s.cron_jobs}
    assert crons["expire_purchase_approvals"].minute == set(range(0, 60, 5))
    assert crons["expire_impersonation_sessions"].minute == set(range(0, 60, 5))
    assert (crons["purge_expired_tokens"].hour, crons["purge_expired_tokens"].minute) == (3, 0)


async def test_every_job_services_enqueue_is_registered() -> None:
    names = {f.name for f in WorkerSettings.functions}
    assert names >= {
        "send_trainer_invite",
        "send_welcome_email",
        "send_verification_email",
        "send_password_reset",
        "send_email_change",
        "notify_parent_approval_request",
        "notify_child_approval_denied",
        "notify_parent_share_link_blocked",
    }
    assert all(f.max_tries == 5 for f in WorkerSettings.functions)


async def test_failure_retries_with_exponential_backoff() -> None:
    async def flaky(ctx: Ctx, **kwargs: str) -> None:
        raise ConnectionError("boom")

    wrapped = with_retries(flaky)
    delays = []
    for attempt in range(1, MAX_TRIES):
        with pytest.raises(Retry) as exc:
            await wrapped({"job_try": attempt})
        delays.append(exc.value.defer_score)
    assert delays == [2000, 4000, 8000, 16000]  # ms


async def test_dead_letter_logged_after_final_failure_without_secrets() -> None:
    async def send_password_reset(ctx: Ctx, **kwargs: str) -> None:
        raise ConnectionError("smtp down for secret-raw-token")

    wrapped = with_retries(send_password_reset)
    with structlog.testing.capture_logs() as logs, pytest.raises(ConnectionError):
        await wrapped({"job_try": MAX_TRIES, "job_id": "j1"}, token="secret-raw-token")
    [entry] = [e for e in logs if e["event"] == "job.dead_letter"]
    assert entry["log_level"] == "error"
    assert (entry["job"], entry["attempts"], entry["error_type"]) == (
        "send_password_reset",
        MAX_TRIES,
        "ConnectionError",
    )
    assert "secret-raw-token" not in str(logs)


async def test_no_dead_letter_before_the_last_attempt() -> None:
    async def failing(ctx: Ctx, **kwargs: str) -> None:
        raise ConnectionError

    with structlog.testing.capture_logs() as logs, pytest.raises(Retry):
        await with_retries(failing)({"job_try": MAX_TRIES - 1})
    assert [e for e in logs if e["event"] == "job.dead_letter"] == []
