from datetime import timedelta
from typing import Any

from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_token
from app.models.identity import (
    OneTimeToken,
    OttPurpose,
    TrainerProfile,
    User,
    UserRole,
    UserStatus,
)
from app.models.platform import AuditLog
from tests.api.helpers import bearer
from tests.factories import MakeUser
from tests.fakes import FakeClock, FakeJobQueue

PROVISION = "/api/v1/admin/users/trainers"
LOGIN = "/api/v1/auth/login"
SETUP = "/api/v1/auth/password/setup"

BODY = {
    "email": "New.Trainer@example.com",
    "first_name": "Nora",
    "last_name": "Net",
    "phone": "+33100000000",
    "business_name": "Net Academy",
    "slug": "net-academy",
    "timezone": "Europe/Paris",
}


def code(response: Any) -> str:
    return str(response.json()["error"]["code"])


async def count_users(db: AsyncSession) -> int:
    return int((await db.execute(select(func.count()).select_from(User))).scalar_one())


async def test_provision_trainer_creates_pending_user_profiles_and_invite_job(
    client: AsyncClient,
    admin_token: str,
    admin: User,
    jobs: FakeJobQueue,
    db_session: AsyncSession,
    clock: FakeClock,
) -> None:
    r = await client.post(PROVISION, json=BODY, headers=bearer(admin_token))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["email"] == "New.Trainer@example.com"
    assert (body["role"], body["status"]) == ("trainer", "pending")
    assert body["must_change_password"] is True and body["email_verified"] is False
    assert (body["business_name"], body["slug"]) == ("Net Academy", "net-academy")
    assert (body["first_name"], body["last_name"], body["phone"]) == ("Nora", "Net", "+33100000000")
    assert body["timezone"] == "Europe/Paris" and body["trainer_id"] == body["id"]
    assert not {k for k in body if "hash" in k or k in ("password", "token")}

    [job] = jobs.enqueued
    assert job.name == "send_trainer_invite"
    assert job.kwargs["user_id"] == body["id"]
    raw = job.kwargs["token"]
    assert raw not in r.text

    token = (
        await db_session.execute(
            select(OneTimeToken).where(OneTimeToken.token_hash == hash_token(raw))
        )
    ).scalar_one()
    assert token.purpose is OttPurpose.trainer_setup
    assert str(token.user_id) == body["id"]
    assert token.expires_at == clock.now() + timedelta(hours=24)

    profile = await db_session.get(TrainerProfile, token.user_id)
    assert profile is not None and profile.slug == "net-academy"
    audit = (
        await db_session.execute(select(AuditLog).where(AuditLog.action == "trainer.created"))
    ).scalar_one()
    assert audit.actor_user_id == admin.id and str(audit.subject_id) == body["id"]
    assert "example.com" not in str(audit.metadata_)


async def test_provision_trainer_rolls_back_all_rows_on_slug_conflict(
    client: AsyncClient,
    admin_token: str,
    make_trainer: MakeUser,
    jobs: FakeJobQueue,
    db_session: AsyncSession,
) -> None:
    taken = await make_trainer()
    profile = await db_session.get(TrainerProfile, taken.id)
    assert profile is not None
    profile.slug = "net-academy"
    await db_session.flush()
    before = await count_users(db_session)

    r = await client.post(PROVISION, json=BODY, headers=bearer(admin_token))
    assert (r.status_code, code(r)) == (409, "SLUG_TAKEN")
    assert await count_users(db_session) == before  # no users row left behind
    assert jobs.enqueued == []
    leftovers = await db_session.execute(select(func.count()).select_from(OneTimeToken))
    assert leftovers.scalar_one() == 0


async def test_provision_duplicate_email_409(
    client: AsyncClient, admin_token: str, make_user: MakeUser, jobs: FakeJobQueue
) -> None:
    await make_user(email="new.trainer@example.com")  # citext: case-insensitive clash
    r = await client.post(PROVISION, json=BODY, headers=bearer(admin_token))
    assert (r.status_code, code(r)) == (409, "EMAIL_TAKEN")
    assert jobs.enqueued == []


async def test_provision_validation_422(client: AsyncClient, admin_token: str) -> None:
    for patch in (
        {"slug": "Bad Slug"},
        {"slug": "ab"},
        {"slug": "-lead"},
        {"slug": "a" * 61},
        {"email": "nope"},
        {"first_name": ""},
        {"business_name": ""},
        {"timezone": "Mars/Base"},
        {"phone": "9" * 33},
        {"role": "super_admin"},
    ):
        r = await client.post(PROVISION, json={**BODY, **patch}, headers=bearer(admin_token))
        assert (r.status_code, code(r)) == (422, "VALIDATION_ERROR"), patch
    minimal = {k: BODY[k] for k in ("email", "first_name", "last_name", "business_name", "slug")}
    ok = await client.post(PROVISION, json=minimal, headers=bearer(admin_token))
    assert ok.status_code == 201
    assert ok.json()["timezone"] == "UTC" and ok.json()["phone"] is None


async def test_provisioned_trainer_cannot_login_until_setup(
    client: AsyncClient, admin_token: str, jobs: FakeJobQueue, db_session: AsyncSession
) -> None:
    r = await client.post(PROVISION, json=BODY, headers=bearer(admin_token))
    assert r.status_code == 201
    guess = await client.post(LOGIN, json={"email": BODY["email"], "password": "Guess-1234-abcd"})
    assert (guess.status_code, code(guess)) == (401, "INVALID_CREDENTIALS")

    password = "Tr1cky-Purple-Elephant-42!"
    setup = await client.post(
        SETUP, json={"token": jobs.enqueued[0].kwargs["token"], "new_password": password}
    )
    assert setup.status_code == 200, setup.text
    login = await client.post(LOGIN, json={"email": BODY["email"], "password": password})
    assert login.status_code == 200
    user = (await db_session.execute(select(User).where(User.email == BODY["email"]))).scalar_one()
    assert (user.status, user.role, user.must_change_password) == (
        UserStatus.active,
        UserRole.trainer,
        False,
    )
