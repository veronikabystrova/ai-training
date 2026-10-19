from datetime import timedelta
from typing import Any
from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.identity import OttPurpose, UserRole
from app.repositories.one_time_token import OneTimeTokenRepository
from app.repositories.refresh_token import RefreshTokenRepository
from tests.api.auth.helpers import LOGIN, REFRESH, login
from tests.api.helpers import bearer
from tests.factories import TEST_PASSWORD, MakeUser, PrincipalToken
from tests.fakes import FakeClock, FakeJobQueue

CHANGE = "/api/v1/me/email-change"
CONFIRM = "/api/v1/me/email-change/confirm"


def code(response: Any) -> str:
    return str(response.json()["error"]["code"])


async def _request(
    client: AsyncClient, token: str, new_email: str, password: str = TEST_PASSWORD
) -> Any:
    return await client.post(
        CHANGE, json={"new_email": new_email, "password": password}, headers=bearer(token)
    )


def _issued_token(jobs: FakeJobQueue) -> str:
    return jobs.enqueued[-1].kwargs["token"]


async def test_email_change_issues_token_to_new_address_old_stays_active(
    client: AsyncClient,
    make_user: MakeUser,
    principal_token: PrincipalToken,
    jobs: FakeJobQueue,
) -> None:
    user = await make_user(email="old@x.com")
    r = await _request(client, principal_token(user), "new@x.com")
    assert (r.status_code, r.json()) == (202, {"status": "accepted"})
    job = jobs.enqueued[-1]
    assert job.name == "send_email_change"
    assert (job.kwargs["user_id"], job.kwargs["new_email"]) == (str(user.id), "new@x.com")
    assert user.email == "old@x.com"
    assert (
        await client.post(LOGIN, json={"email": "old@x.com", "password": TEST_PASSWORD})
    ).status_code == 200


async def test_email_change_wrong_password_403(
    client: AsyncClient,
    make_user: MakeUser,
    principal_token: PrincipalToken,
    jobs: FakeJobQueue,
) -> None:
    user = await make_user()
    r = await _request(client, principal_token(user), "new@x.com", password="wrong-password-1")
    assert (r.status_code, code(r)) == (403, "INVALID_CURRENT_PASSWORD")
    assert jobs.enqueued == []


async def test_email_change_to_taken_address_409(
    client: AsyncClient,
    make_user: MakeUser,
    principal_token: PrincipalToken,
    jobs: FakeJobQueue,
) -> None:
    user = await make_user()
    await make_user(email="taken@x.com")
    r = await _request(client, principal_token(user), "TAKEN@x.com")
    assert (r.status_code, code(r)) == (409, "EMAIL_TAKEN")
    assert jobs.enqueued == []


async def test_email_change_validates_body(
    client: AsyncClient, make_user: MakeUser, principal_token: PrincipalToken
) -> None:
    token = principal_token(await make_user())
    for body in (
        {"new_email": "not-an-email", "password": TEST_PASSWORD},
        {"new_email": "a@x.com"},
        {"new_email": "a@x.com", "password": TEST_PASSWORD, "role": "super_admin"},
    ):
        r = await client.post(CHANGE, json=body, headers=bearer(token))
        assert (r.status_code, code(r)) == (422, "VALIDATION_ERROR"), body
    assert (
        await client.post(CHANGE, json={"new_email": "a@x.com", "password": "x"})
    ).status_code == 401


async def test_email_change_confirm_swaps_and_revokes_families(
    client: AsyncClient,
    make_user: MakeUser,
    principal_token: PrincipalToken,
    jobs: FakeJobQueue,
    db_session: AsyncSession,
) -> None:
    user = await make_user(email="old@x.com")
    user.is_email_verified = False
    refresh = (await login(client, "old@x.com"))["tokens"]["refresh_token"]
    await _request(client, principal_token(user), "New@x.com")
    token = _issued_token(jobs)

    r = await client.post(CONFIRM, json={"token": token})  # public: no Authorization header
    assert (r.status_code, r.content) == (204, b"")
    await db_session.refresh(user)
    assert (user.email, user.is_email_verified) == ("New@x.com", True)

    gone = await client.post(REFRESH, json={"refresh_token": refresh})
    assert (gone.status_code, code(gone)) == (401, "INVALID_REFRESH_TOKEN")
    assert (
        await client.post(LOGIN, json={"email": "old@x.com", "password": TEST_PASSWORD})
    ).status_code == 401
    assert (
        await client.post(LOGIN, json={"email": "new@x.com", "password": TEST_PASSWORD})
    ).status_code == 200
    audit = (await db_session.execute(text("select action, actor_user_id from audit_log"))).all()
    assert ("me.email_changed", user.id) in audit

    again = await client.post(CONFIRM, json={"token": token})
    assert (again.status_code, code(again)) == (400, "TOKEN_INVALID")


async def test_email_change_confirm_conflict_409_and_token_not_consumed(
    client: AsyncClient,
    make_user: MakeUser,
    principal_token: PrincipalToken,
    jobs: FakeJobQueue,
    db_session: AsyncSession,
) -> None:
    user = await make_user(email="old@x.com")
    await _request(client, principal_token(user), "race@x.com")
    token = _issued_token(jobs)
    await make_user(email="race@x.com")  # registered between request and confirm

    r = await client.post(CONFIRM, json={"token": token})
    assert (r.status_code, code(r)) == (409, "EMAIL_TAKEN")
    await db_session.refresh(user)
    assert user.email == "old@x.com"
    used = (
        await db_session.execute(
            text("select used_at from one_time_tokens where purpose = 'email_change'")
        )
    ).scalar_one()
    assert used is None


async def test_email_change_confirm_rejects_bad_expired_and_foreign_purpose_tokens(
    client: AsyncClient,
    make_user: MakeUser,
    principal_token: PrincipalToken,
    jobs: FakeJobQueue,
    clock: FakeClock,
    db_session: AsyncSession,
) -> None:
    user = await make_user()
    unknown = await client.post(CONFIRM, json={"token": "x" * 40})
    assert (unknown.status_code, code(unknown)) == (400, "TOKEN_INVALID")

    other = await OneTimeTokenRepository(db_session).issue(
        user_id=user.id,
        purpose=OttPurpose.email_verify,
        ttl=clock.now() - clock.now().replace(year=2020),
        now=clock.now(),
    )
    wrong_purpose = await client.post(CONFIRM, json={"token": other})
    assert code(wrong_purpose) == "TOKEN_INVALID"

    await _request(client, principal_token(user), "new@x.com")
    token = _issued_token(jobs)
    clock.advance(hours=24, seconds=1)
    expired = await client.post(CONFIRM, json={"token": token})
    assert (expired.status_code, code(expired)) == (400, "TOKEN_INVALID")


async def test_second_request_supersedes_the_first_token(
    client: AsyncClient,
    make_user: MakeUser,
    principal_token: PrincipalToken,
    jobs: FakeJobQueue,
) -> None:
    user = await make_user(role=UserRole.player)
    token = principal_token(user)
    await _request(client, token, "one@x.com")
    first = _issued_token(jobs)
    await _request(client, token, "two@x.com")
    stale = await client.post(CONFIRM, json={"token": first})
    assert code(stale) == "TOKEN_INVALID"
    ok = await client.post(CONFIRM, json={"token": _issued_token(jobs)})
    assert ok.status_code == 204
    assert user.email == "two@x.com"


async def test_confirm_does_not_touch_other_users_families(
    client: AsyncClient,
    make_user: MakeUser,
    principal_token: PrincipalToken,
    jobs: FakeJobQueue,
    db_session: AsyncSession,
    clock: FakeClock,
) -> None:
    user = await make_user()
    bystander = await make_user(email="by@x.com")
    survivor = await RefreshTokenRepository(db_session).issue(
        user_id=bystander.id,
        family_id=uuid4(),
        expires_at=clock.now() + timedelta(days=30),
    )
    await _request(client, principal_token(user), "n@x.com")
    await client.post(CONFIRM, json={"token": _issued_token(jobs)})
    assert (await client.post(REFRESH, json={"refresh_token": survivor})).status_code == 200
