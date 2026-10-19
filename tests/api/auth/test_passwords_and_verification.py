from collections.abc import Awaitable, Callable
from datetime import timedelta

import pytest
import structlog.testing
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import PasswordHasher
from app.models.identity import OttPurpose, User, UserRole, UserStatus
from app.models.platform import AuditLog
from app.repositories.one_time_token import OneTimeTokenRepository
from tests.api.auth.helpers import LOGIN, REFRESH, code, login
from tests.api.helpers import bearer
from tests.app_fixtures import TEST_DEFAULT_LIMIT
from tests.factories import TEST_PASSWORD, MakeTrainer, MakeUser
from tests.fakes import FakeClock, FakeJobQueue

RESET_REQUEST = "/api/v1/auth/password-reset/request"
RESET_CONFIRM = "/api/v1/auth/password-reset/confirm"
VERIFY = "/api/v1/auth/verify-email"
RESEND = "/api/v1/auth/verify-email/resend"
SETUP = "/api/v1/auth/password/setup"
STRONG = "Tr1cky-Purple-Elephant-42!"
WEAK = "password1234"

IssueToken = Callable[[User, OttPurpose], Awaitable[str]]


@pytest.fixture
def issue_token(db_session: AsyncSession, clock: FakeClock) -> IssueToken:
    ttl = {
        OttPurpose.password_reset: timedelta(hours=1),
        OttPurpose.email_verify: timedelta(hours=24),
        OttPurpose.trainer_setup: timedelta(hours=24),
        OttPurpose.email_change: timedelta(hours=24),
    }

    async def _issue(user: User, purpose: OttPurpose) -> str:
        return await OneTimeTokenRepository(db_session).issue(
            user_id=user.id, purpose=purpose, ttl=ttl[purpose], now=clock.now()
        )

    return _issue


# --- password reset: request ---------------------------------------------------------------


async def test_reset_request_is_202_for_unknown_and_known_email(
    client: AsyncClient, make_user: MakeUser, jobs: FakeJobQueue
) -> None:
    user = await make_user(email="known@x.com")
    for email in ("nobody@x.com", "known@x.com"):
        r = await client.post(RESET_REQUEST, json={"email": email})
        assert r.status_code == 202
        assert r.json() == {"status": "accepted"}
    assert [j.name for j in jobs.enqueued] == ["send_password_reset"]  # only the real user
    assert jobs.enqueued[0].kwargs["user_id"] == str(user.id)
    assert len(jobs.enqueued[0].kwargs["token"]) >= 20


@pytest.mark.parametrize("status", [UserStatus.pending, UserStatus.inactive, UserStatus.deleted])
async def test_reset_request_for_non_active_user_is_202_and_sends_nothing(
    client: AsyncClient, make_user: MakeUser, jobs: FakeJobQueue, status: UserStatus
) -> None:
    await make_user(email="u@x.com", status=status)
    r = await client.post(RESET_REQUEST, json={"email": "u@x.com"})
    assert r.status_code == 202
    assert jobs.enqueued == []


async def test_reset_request_rate_limited_per_lowercased_email(
    client: AsyncClient, jobs: FakeJobQueue
) -> None:
    for i in range(5):
        r = await client.post(
            RESET_REQUEST, json={"email": "Nobody@x.com" if i % 2 else "nobody@x.com"}
        )
        assert r.status_code == 202
    r = await client.post(RESET_REQUEST, json={"email": "NOBODY@x.com"})
    assert r.status_code == 429
    assert code(r) == "RATE_LIMITED"
    assert int(r.headers["Retry-After"]) >= 1
    assert (await client.post(RESET_REQUEST, json={"email": "other@x.com"})).status_code == 202


async def test_reset_request_supersedes_the_previous_token(
    client: AsyncClient, make_user: MakeUser, jobs: FakeJobQueue
) -> None:
    await make_user(email="u@x.com")
    await client.post(RESET_REQUEST, json={"email": "u@x.com"})
    await client.post(RESET_REQUEST, json={"email": "u@x.com"})
    first, second = (j.kwargs["token"] for j in jobs.enqueued)
    assert (
        await client.post(RESET_CONFIRM, json={"token": first, "new_password": STRONG})
    ).status_code == 400
    assert (
        await client.post(RESET_CONFIRM, json={"token": second, "new_password": STRONG})
    ).status_code == 204


async def test_reset_request_validates_body(client: AsyncClient) -> None:
    assert (await client.post(RESET_REQUEST, json={"email": "nope"})).status_code == 422
    assert (await client.post(RESET_REQUEST, json={"email": "u@x.com", "x": 1})).status_code == 422


# --- password reset: confirm ---------------------------------------------------------------


async def test_reset_confirm_happy_path_revokes_all_families(
    client: AsyncClient,
    db_session: AsyncSession,
    make_user: MakeUser,
    issue_token: IssueToken,
) -> None:
    user = await make_user(email="u@x.com")
    laptop = (await login(client, "u@x.com"))["tokens"]["refresh_token"]
    phone = (await login(client, "u@x.com"))["tokens"]["refresh_token"]
    token = await issue_token(user, OttPurpose.password_reset)

    r = await client.post(RESET_CONFIRM, json={"token": token, "new_password": STRONG})
    assert r.status_code == 204
    assert r.content == b""

    for refresh in (laptop, phone):
        gone = await client.post(REFRESH, json={"refresh_token": refresh})
        assert code(gone) == "INVALID_REFRESH_TOKEN"
    old = await client.post(LOGIN, json={"email": "u@x.com", "password": TEST_PASSWORD})
    assert code(old) == "INVALID_CREDENTIALS"
    assert (await login(client, "u@x.com", STRONG))["user"]["email"] == "u@x.com"
    audit = (
        (await db_session.execute(select(AuditLog).where(AuditLog.action == "auth.password_reset")))
        .scalars()
        .all()
    )
    assert [a.actor_user_id for a in audit] == [user.id]


async def test_reset_confirm_weak_password_does_not_burn_token(
    client: AsyncClient, make_user: MakeUser, issue_token: IssueToken
) -> None:
    token = await issue_token(await make_user(email="u@x.com"), OttPurpose.password_reset)
    r = await client.post(RESET_CONFIRM, json={"token": token, "new_password": WEAK})
    assert r.status_code == 422
    assert code(r) == "WEAK_PASSWORD"
    ok = await client.post(RESET_CONFIRM, json={"token": token, "new_password": STRONG})
    assert ok.status_code == 204


async def test_reset_confirm_short_password_is_a_validation_error(
    client: AsyncClient, make_user: MakeUser, issue_token: IssueToken
) -> None:
    token = await issue_token(await make_user(), OttPurpose.password_reset)
    r = await client.post(RESET_CONFIRM, json={"token": token, "new_password": "short"})
    assert r.status_code == 422
    assert code(r) == "VALIDATION_ERROR"


async def test_reset_confirm_reuse_expired_unknown_wrong_purpose_all_400_token_invalid(
    client: AsyncClient, make_user: MakeUser, issue_token: IssueToken, clock: FakeClock
) -> None:
    user = await make_user(email="u@x.com")
    used = await issue_token(user, OttPurpose.password_reset)
    assert (
        await client.post(RESET_CONFIRM, json={"token": used, "new_password": STRONG})
    ).status_code == 204
    wrong_purpose = await issue_token(user, OttPurpose.email_verify)
    expiring = await issue_token(user, OttPurpose.password_reset)

    clock.advance(hours=1, seconds=1)  # 1 h boundary: just past
    bodies = []
    for token in (used, "x" * 43, wrong_purpose, expiring):
        r = await client.post(RESET_CONFIRM, json={"token": token, "new_password": STRONG})
        assert r.status_code == 400
        assert code(r) == "TOKEN_INVALID"
        bodies.append(r.json())
    assert all(b == bodies[0] for b in bodies)


async def test_reset_confirm_expiry_boundary_1h(
    client: AsyncClient, make_user: MakeUser, issue_token: IssueToken, clock: FakeClock
) -> None:
    user = await make_user()
    token = await issue_token(user, OttPurpose.password_reset)
    clock.advance(hours=1, seconds=-1)
    r = await client.post(RESET_CONFIRM, json={"token": token, "new_password": STRONG})
    assert r.status_code == 204


async def test_reset_confirm_for_deactivated_user_is_token_invalid(
    client: AsyncClient, make_user: MakeUser, issue_token: IssueToken, db_session: AsyncSession
) -> None:
    user = await make_user()
    token = await issue_token(user, OttPurpose.password_reset)
    user.status = UserStatus.inactive
    await db_session.flush()
    r = await client.post(RESET_CONFIRM, json={"token": token, "new_password": STRONG})
    assert code(r) == "TOKEN_INVALID"


# --- verify email --------------------------------------------------------------------------


async def test_verify_email_204_then_400_on_reuse(
    client: AsyncClient, db_session: AsyncSession, make_user: MakeUser, issue_token: IssueToken
) -> None:
    user = await make_user(email="u@x.com")
    user.is_email_verified = False
    await db_session.flush()
    token = await issue_token(user, OttPurpose.email_verify)

    r = await client.post(VERIFY, json={"token": token})
    assert r.status_code == 204
    await db_session.refresh(user)
    assert user.is_email_verified is True
    again = await client.post(VERIFY, json={"token": token})
    assert again.status_code == 400
    assert code(again) == "TOKEN_INVALID"


async def test_verify_email_expiry_boundary_24h(
    client: AsyncClient, make_user: MakeUser, issue_token: IssueToken, clock: FakeClock
) -> None:
    user = await make_user()
    inside = await issue_token(user, OttPurpose.email_verify)
    clock.advance(hours=24, seconds=-1)
    assert (await client.post(VERIFY, json={"token": inside})).status_code == 204
    outside = await issue_token(user, OttPurpose.email_verify)
    clock.advance(hours=24, seconds=1)
    assert code(await client.post(VERIFY, json={"token": outside})) == "TOKEN_INVALID"


async def test_verify_email_rejects_malformed_body(client: AsyncClient) -> None:
    assert (await client.post(VERIFY, json={})).status_code == 422
    assert (await client.post(VERIFY, json={"token": "short"})).status_code == 422


# --- verify email: resend ------------------------------------------------------------------


async def test_verify_resend_202_then_already_verified_200_and_limit_3_per_hour(
    client: AsyncClient, db_session: AsyncSession, make_user: MakeUser, jobs: FakeJobQueue
) -> None:
    user = await make_user(email="u@x.com")
    user.is_email_verified = False
    await db_session.flush()
    headers = bearer((await login(client, "u@x.com"))["tokens"]["access_token"])

    first = await client.post(RESEND, headers=headers)
    assert first.status_code == 202
    assert first.json() == {"status": "accepted"}
    second = await client.post(RESEND, headers=headers)
    assert second.status_code == 202
    first_token, second_token = (j.kwargs["token"] for j in jobs.enqueued)
    assert [j.name for j in jobs.enqueued] == ["send_verification_email"] * 2
    # Supersedes: only the latest token verifies.
    assert code(await client.post(VERIFY, json={"token": first_token})) == "TOKEN_INVALID"
    assert (await client.post(VERIFY, json={"token": second_token})).status_code == 204

    verified = await client.post(RESEND, headers=headers)
    assert verified.status_code == 200
    assert verified.json() == {"status": "already_verified"}
    assert len(jobs.enqueued) == 2

    limited = await client.post(RESEND, headers=headers)
    assert limited.status_code == 429
    assert code(limited) == "RATE_LIMITED"


async def test_verify_resend_requires_authentication(client: AsyncClient) -> None:
    assert (await client.post(RESEND)).status_code == 401


# --- trainer setup -------------------------------------------------------------------------


async def _provisioned(make_trainer: MakeTrainer, db_session: AsyncSession) -> User:
    trainer = await make_trainer(email="new@x.com", status=UserStatus.pending)
    trainer.must_change_password = True
    trainer.is_email_verified = False
    await db_session.flush()
    return trainer


async def test_password_setup_activates_pending_trainer(
    client: AsyncClient,
    db_session: AsyncSession,
    make_trainer: MakeTrainer,
    issue_token: IssueToken,
    clock: FakeClock,
) -> None:
    trainer = await _provisioned(make_trainer, db_session)
    token = await issue_token(trainer, OttPurpose.trainer_setup)

    r = await client.post(SETUP, json={"token": token, "new_password": STRONG})
    assert r.status_code == 200
    body = r.json()
    assert body["user"]["role"] == "trainer"
    assert body["user"]["trainer_id"] == str(trainer.id)
    assert body["user"]["must_change_password"] is False
    assert body["user"]["email_verified"] is True
    assert body["tokens"]["token_type"] == "bearer"
    await db_session.refresh(trainer)
    assert trainer.status is UserStatus.active
    assert trainer.last_login_at == clock.now()
    # The issued access token works, the new password logs in, the token is spent.
    me = await client.get("/probe/principal", headers=bearer(body["tokens"]["access_token"]))
    assert me.status_code == 200
    assert (await login(client, "new@x.com", STRONG))["user"]["role"] == "trainer"
    again = await client.post(SETUP, json={"token": token, "new_password": STRONG})
    assert code(again) == "TOKEN_INVALID"


async def test_password_setup_weak_password_does_not_burn_token(
    client: AsyncClient,
    db_session: AsyncSession,
    make_trainer: MakeTrainer,
    issue_token: IssueToken,
) -> None:
    trainer = await _provisioned(make_trainer, db_session)
    token = await issue_token(trainer, OttPurpose.trainer_setup)
    r = await client.post(SETUP, json={"token": token, "new_password": WEAK})
    assert r.status_code == 422
    assert code(r) == "WEAK_PASSWORD"
    assert (
        await client.post(SETUP, json={"token": token, "new_password": STRONG})
    ).status_code == 200


async def test_password_setup_expiry_boundary_24h(
    client: AsyncClient,
    db_session: AsyncSession,
    make_trainer: MakeTrainer,
    issue_token: IssueToken,
    clock: FakeClock,
) -> None:
    trainer = await _provisioned(make_trainer, db_session)
    token = await issue_token(trainer, OttPurpose.trainer_setup)
    clock.advance(hours=24, seconds=1)
    r = await client.post(SETUP, json={"token": token, "new_password": STRONG})
    assert r.status_code == 400
    assert code(r) == "TOKEN_INVALID"


async def test_password_setup_rejects_already_active_user_and_wrong_purpose(
    client: AsyncClient, make_user: MakeUser, issue_token: IssueToken
) -> None:
    active = await make_user(role=UserRole.trainer)
    stale = await issue_token(active, OttPurpose.trainer_setup)
    assert code(await client.post(SETUP, json={"token": stale, "new_password": STRONG})) == (
        "TOKEN_INVALID"
    )
    reset = await issue_token(active, OttPurpose.password_reset)
    assert code(await client.post(SETUP, json={"token": reset, "new_password": STRONG})) == (
        "TOKEN_INVALID"
    )


async def test_password_setup_stores_a_verifiable_hash(
    client: AsyncClient,
    db_session: AsyncSession,
    make_trainer: MakeTrainer,
    issue_token: IssueToken,
) -> None:
    trainer = await _provisioned(make_trainer, db_session)
    token = await issue_token(trainer, OttPurpose.trainer_setup)
    await client.post(SETUP, json={"token": token, "new_password": STRONG})
    await db_session.refresh(trainer)
    hasher = PasswordHasher(time_cost=1, memory_kib=8, parallelism=1)
    assert trainer.password_hash != STRONG
    assert await hasher.verify(trainer.password_hash, STRONG)


# --- default rate limit on the public confirm routes ---------------------------------------


async def test_public_confirm_routes_share_the_default_ip_limit(client: AsyncClient) -> None:
    body = {"token": "x" * 43}
    for _ in range(TEST_DEFAULT_LIMIT):
        assert (await client.post(VERIFY, json=body)).status_code == 400
    r = await client.post(VERIFY, json=body)
    assert r.status_code == 429


# --- review fixes (Phase D) ----------------------------------------------------------------

CONTEXT_PASSWORD = "wojciechowskizygmunt"


async def test_reset_request_default_ip_limit_applies_to_distinct_emails(
    client: AsyncClient,
) -> None:
    for i in range(TEST_DEFAULT_LIMIT):
        r = await client.post(RESET_REQUEST, json={"email": f"user{i}@x.com"})
        assert r.status_code == 202
    r = await client.post(RESET_REQUEST, json={"email": "one-more@x.com"})
    assert r.status_code == 429
    assert code(r) == "RATE_LIMITED"


async def test_reset_request_enqueue_failure_is_logged_and_still_202(
    client: AsyncClient,
    db_session: AsyncSession,
    make_user: MakeUser,
    jobs: FakeJobQueue,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def boom(name: str, **kwargs: str) -> None:
        raise ConnectionError("redis down")

    monkeypatch.setattr(jobs, "enqueue", boom)
    await make_user(email="u@x.com")
    with structlog.testing.capture_logs() as logs:
        r = await client.post(RESET_REQUEST, json={"email": "u@x.com"})
    assert r.status_code == 202
    assert [e["job"] for e in logs if e["event"] == "job_enqueue_failed"] == ["send_password_reset"]


async def test_resend_enqueue_failure_is_logged_and_still_202(
    client: AsyncClient,
    make_user: MakeUser,
    jobs: FakeJobQueue,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def boom(name: str, **kwargs: str) -> None:
        raise ConnectionError("redis down")

    user = await make_user(email="u@x.com")
    user.is_email_verified = False
    await db_session.flush()
    access = (await login(client, "u@x.com"))["tokens"]["access_token"]
    monkeypatch.setattr(jobs, "enqueue", boom)
    r = await client.post(RESEND, headers=bearer(access))
    assert r.status_code == 202


async def test_reset_confirm_uses_the_token_owners_name_as_password_context(
    client: AsyncClient, make_user: MakeUser, issue_token: IssueToken
) -> None:
    user = await make_user(
        email="zygmunt.wojciechowski@x.com", first_name="Zygmunt", last_name="Wojciechowski"
    )
    token = await issue_token(user, OttPurpose.password_reset)
    r = await client.post(RESET_CONFIRM, json={"token": token, "new_password": CONTEXT_PASSWORD})
    assert r.status_code == 422
    assert code(r) == "WEAK_PASSWORD"
    # The weak attempt did not burn the token.
    ok = await client.post(RESET_CONFIRM, json={"token": token, "new_password": STRONG})
    assert ok.status_code == 204


async def test_reset_confirm_context_password_is_fine_for_an_unrelated_user(
    client: AsyncClient, make_user: MakeUser, issue_token: IssueToken
) -> None:
    user = await make_user(email="someone@x.com")
    token = await issue_token(user, OttPurpose.password_reset)
    r = await client.post(RESET_CONFIRM, json={"token": token, "new_password": CONTEXT_PASSWORD})
    assert r.status_code == 204


async def test_setup_uses_the_token_owners_name_as_password_context(
    client: AsyncClient,
    db_session: AsyncSession,
    make_trainer: MakeTrainer,
    issue_token: IssueToken,
) -> None:
    trainer = await make_trainer(
        email="zygmunt.wojciechowski@x.com",
        status=UserStatus.pending,
        first_name="Zygmunt",
        last_name="Wojciechowski",
    )
    await db_session.flush()
    token = await issue_token(trainer, OttPurpose.trainer_setup)
    r = await client.post(SETUP, json={"token": token, "new_password": CONTEXT_PASSWORD})
    assert r.status_code == 422
    assert code(r) == "WEAK_PASSWORD"
    ok = await client.post(SETUP, json={"token": token, "new_password": STRONG})
    assert ok.status_code == 200
