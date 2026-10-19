import hashlib
from datetime import timedelta
from typing import Any

from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import new_id
from app.models.identity import (
    AvailabilityWindow,
    LinkStatus,
    OneTimeToken,
    OttPurpose,
    Profile,
    RefreshToken,
    TrainerPlayerLink,
    TrainerProfile,
    User,
    UserErasure,
    UserRole,
    UserStatus,
)
from app.models.platform import AuditLog
from app.repositories.one_time_token import OneTimeTokenRepository
from app.repositories.refresh_token import RefreshTokenRepository
from tests.api.auth.helpers import LOGIN
from tests.api.helpers import bearer
from tests.factories import (
    AddShareLink,
    AddWindow,
    LinkPlayer,
    MakeCoach,
    MakeDependent,
    MakePlayer,
    MakeUser,
    PrincipalToken,
)
from tests.fakes import FakeClock

USERS = "/api/v1/admin/users"
EMAIL = "victim.person@example.com"


def code(response: Any) -> str:
    return str(response.json()["error"]["code"])


async def test_erase_anonymises_in_place_and_writes_ledger(
    client: AsyncClient,
    admin_token: str,
    admin: User,
    make_user: MakeUser,
    principal_token: PrincipalToken,
    db_session: AsyncSession,
    clock: FakeClock,
) -> None:
    victim = await make_user(email=EMAIL, first_name="Vic", last_name="Tim")
    profile = await db_session.get(Profile, victim.id)
    assert profile is not None
    profile.phone, profile.school, profile.avatar_key = "+33", "School", "avatars/x"
    victim.last_login_at = clock.now()
    raw = await RefreshTokenRepository(db_session).issue(
        user_id=victim.id, family_id=new_id(), expires_at=clock.now() + timedelta(days=30)
    )
    token = principal_token(victim)
    await db_session.flush()

    r = await client.delete(f"{USERS}/{victim.id}", headers=bearer(admin_token))
    assert r.status_code == 204 and r.content == b""

    for obj in (victim, profile):
        await db_session.refresh(obj)
    assert victim.email == f"deleted-{victim.id}@erased.invalid"
    assert victim.status is UserStatus.deleted and victim.deleted_at == clock.now()
    assert victim.password_hash == "!" and victim.last_login_at is None
    assert victim.is_email_verified is False
    assert (profile.first_name, profile.last_name) == ("Deleted", "User")
    assert (profile.phone, profile.school, profile.avatar_key) == (None, None, None)

    ledger = (
        await db_session.execute(
            select(UserErasure).where(UserErasure.original_user_id == victim.id)
        )
    ).scalar_one()
    assert ledger.original_email_hash == hashlib.sha256(EMAIL.encode()).hexdigest()
    assert ledger.erased_by_user_id == admin.id
    assert ledger.legal_retention_until.year == clock.now().year + 7
    assert ledger.legal_retention_until.month == clock.now().month

    rows = (
        (await db_session.execute(select(RefreshToken).where(RefreshToken.user_id == victim.id)))
        .scalars()
        .all()
    )
    assert rows and all(row.revoked_at is not None for row in rows)
    assert raw
    assert (await client.get("/probe/principal", headers=bearer(token))).status_code == 401
    login = await client.post(LOGIN, json={"email": EMAIL, "password": "Correct-Horse-Battery-9"})
    assert login.status_code == 401

    audit = (
        await db_session.execute(select(AuditLog).where(AuditLog.action == "user.erased"))
    ).scalar_one()
    assert audit.actor_user_id == admin.id and audit.subject_id == victim.id
    assert "victim" not in str(audit.metadata_)

    detail = await client.get(f"{USERS}/{victim.id}", headers=bearer(admin_token))
    assert detail.json()["status"] == "deleted" and detail.json()["deleted_at"] is not None


async def test_erase_deletes_one_time_tokens_and_erases_dependents(
    client: AsyncClient,
    admin_token: str,
    make_user: MakeUser,
    make_trainer: MakeUser,
    make_player: MakePlayer,
    make_dependent: MakeDependent,
    link_player: LinkPlayer,
    add_window: AddWindow,
    db_session: AsyncSession,
    clock: FakeClock,
) -> None:
    trainer = await make_trainer()
    parent = await make_user(email=EMAIL)
    own = await make_player(owner=parent, display_name="Vic Tim")
    child = await make_dependent(parent=parent, with_login=True)
    child.emergency_contact_name, child.emergency_contact_phone = "Mum", "+3311"
    child.school, child.skill_level, child.jersey_number = "School", "pro", 7
    await link_player(trainer, own)
    await link_player(trainer, child)
    await add_window(own.id, weekday=1, start="09:00", end="10:00")
    await add_window(child.id, weekday=2, start="09:00", end="10:00")
    child_login = await db_session.get(User, child.login_user_id)
    assert child_login is not None
    child_email = child_login.email
    otts = OneTimeTokenRepository(db_session)
    for owner in (parent, child_login):
        await otts.issue(
            user_id=owner.id,
            purpose=OttPurpose.email_change,
            ttl=timedelta(hours=1),
            now=clock.now(),
            payload={"new_email": f"new-{owner.id}@example.com"},
        )
    bystander = await make_user()
    await otts.issue(
        user_id=bystander.id,
        purpose=OttPurpose.email_verify,
        ttl=timedelta(hours=1),
        now=clock.now(),
    )

    r = await client.delete(f"{USERS}/{parent.id}", headers=bearer(admin_token))
    assert r.status_code == 204

    tokens = (await db_session.execute(select(OneTimeToken.user_id))).scalars().all()
    assert tokens == [bystander.id]  # the erased users' tokens are gone, others untouched

    for profile in (own, child):
        await db_session.refresh(profile)
    assert own.display_name == child.display_name == "Deleted Player"
    scrubbed = [
        getattr(child, name)
        for name in (
            "emergency_contact_name",
            "emergency_contact_phone",
            "school",
            "skill_level",
            "jersey_number",
        )
    ]
    assert scrubbed == [None] * 5
    assert own.is_active is False and child.is_active is False
    links = (await db_session.execute(select(TrainerPlayerLink.status))).scalars().all()
    assert links and all(status is LinkStatus.inactive for status in links)
    windows = (await db_session.execute(select(AvailabilityWindow))).scalars().all()
    assert windows == []

    await db_session.refresh(child_login)
    assert child_login.status is UserStatus.deleted
    assert child_login.email == f"deleted-{child_login.id}@erased.invalid" != child_email
    ledger_ids = set(
        (await db_session.execute(select(UserErasure.original_user_id))).scalars().all()
    )
    assert ledger_ids == {parent.id, child_login.id}
    other = await db_session.get(User, bystander.id)
    assert other is not None and other.status is UserStatus.active


async def test_erase_twice_409_already_erased(
    client: AsyncClient, admin_token: str, make_user: MakeUser
) -> None:
    user = await make_user()
    assert (
        await client.delete(f"{USERS}/{user.id}", headers=bearer(admin_token))
    ).status_code == 204
    again = await client.delete(f"{USERS}/{user.id}", headers=bearer(admin_token))
    assert (again.status_code, code(again)) == (409, "ALREADY_ERASED")  # no self-guard either
    unknown = await client.delete(
        f"{USERS}/00000000-0000-7000-8000-000000000000", headers=bearer(admin_token)
    )
    assert (unknown.status_code, code(unknown)) == (404, "NOT_FOUND")


async def test_admin_can_erase_themselves_no_self_guard(
    client: AsyncClient, admin_token: str, admin: User
) -> None:
    r = await client.delete(f"{USERS}/{admin.id}", headers=bearer(admin_token))
    assert r.status_code == 204


async def test_erase_scrubs_trainer_business_details(
    client: AsyncClient, admin_token: str, make_trainer: MakeUser, db_session: AsyncSession
) -> None:
    trainer = await make_trainer()
    profile = await db_session.get(TrainerProfile, trainer.id)
    assert profile is not None
    profile.address, profile.website = "1 Rue X", "https://x.example"
    profile.description, profile.logo_key = "About me", "logos/abc"
    await db_session.flush()

    assert (
        await client.delete(f"{USERS}/{trainer.id}", headers=bearer(admin_token))
    ).status_code == 204
    await db_session.refresh(profile)
    kept = [getattr(profile, n) for n in ("address", "website", "description", "logo_key")]
    assert kept == [None] * 4


async def test_after_erasure_no_plaintext_email_in_any_text_or_jsonb_column(
    client: AsyncClient,
    admin_token: str,
    make_user: MakeUser,
    make_trainer: MakeUser,
    make_coach: MakeCoach,
    make_dependent: MakeDependent,
    add_share_link: AddShareLink,
    db_session: AsyncSession,
    clock: FakeClock,
) -> None:
    trainer = await make_trainer()
    victim = await make_user(email=EMAIL)
    child = await make_dependent(parent=victim, with_login=True)
    child_login = await db_session.get(User, child.login_user_id)
    assert child_login is not None
    emails = [EMAIL, child_login.email, "pending.change@example.com"]
    # every place the address lives: a coach invitation, a pending change, a coach's own account
    invite = await add_share_link(trainer, kind="coach_unique", target_email=EMAIL.upper())
    child_invite = await add_share_link(
        trainer, kind="coach_unique", target_email=child_login.email
    )
    await OneTimeTokenRepository(db_session).issue(
        user_id=victim.id,
        purpose=OttPurpose.email_change,
        ttl=timedelta(hours=1),
        now=clock.now(),
        payload={"new_email": emails[2]},
    )
    coach = await make_coach(
        trainer, user=await make_user(email="coach.victim@example.com", role=UserRole.coach)
    )
    emails.append(coach.user.email)

    for uid in (victim.id, coach.user_id):
        r = await client.delete(f"{USERS}/{uid}", headers=bearer(admin_token))
        assert r.status_code == 204

    columns = (
        await db_session.execute(
            text(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name <> 'alembic_version' "
                "AND udt_name IN ('text', 'varchar', 'bpchar', 'citext', 'jsonb', 'json')"
            )
        )
    ).all()
    assert len(columns) > 20
    for table, column in columns:
        for email in emails:
            hits = (
                await db_session.execute(
                    text(  # identifiers come from information_schema
                        f'SELECT count(*) FROM "{table}" WHERE "{column}"::text ILIKE :needle'  # noqa: S608
                    ),
                    {"needle": f"%{email}%"},
                )
            ).scalar_one()
            assert hits == 0, f"{email} still in {table}.{column}"

    for link in (invite, child_invite):
        await db_session.refresh(link)
        assert link.is_active is False
        assert link.target_email is not None and link.target_email.endswith("@erased.invalid")


async def test_erased_user_cannot_be_updated_or_reactivated_409_user_erased(
    client: AsyncClient, admin_token: str, make_user: MakeUser
) -> None:
    user = await make_user()
    headers = bearer(admin_token)
    await client.delete(f"{USERS}/{user.id}", headers=headers)
    for method, path, body in (
        ("PATCH", f"{USERS}/{user.id}", {"first_name": "Back"}),
        ("POST", f"{USERS}/{user.id}/reactivate", None),
        ("POST", f"{USERS}/{user.id}/deactivate", None),
    ):
        r = await client.request(method, path, json=body, headers=headers)
        assert (r.status_code, code(r)) == (409, "USER_ERASED"), path
