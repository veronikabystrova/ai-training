from datetime import timedelta
from typing import Any

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import new_id
from app.models.identity import RefreshToken, TrainerProfile, UserStatus
from app.models.platform import AuditLog
from app.repositories.refresh_token import RefreshTokenRepository
from tests.api.auth.helpers import REFRESH
from tests.api.helpers import bearer
from tests.factories import AddShareLink, MakeCoach, MakeUser, PrincipalToken
from tests.fakes import FakeClock

USERS = "/api/v1/admin/users"


def code(response: Any) -> str:
    return str(response.json()["error"]["code"])


async def issue_refresh(db: AsyncSession, user_id: Any, clock: FakeClock) -> tuple[str, Any]:
    family = new_id()
    raw = await RefreshTokenRepository(db).issue(
        user_id=user_id, family_id=family, expires_at=clock.now() + timedelta(days=30)
    )
    return raw, family


async def test_deactivate_revokes_families_and_blocks_next_request_immediately(
    client: AsyncClient,
    admin_token: str,
    admin: Any,
    make_user: MakeUser,
    principal_token: PrincipalToken,
    db_session: AsyncSession,
    clock: FakeClock,
) -> None:
    user = await make_user()
    token = principal_token(user)
    raw, family = await issue_refresh(db_session, user.id, clock)
    other_raw, _ = await issue_refresh(db_session, user.id, clock)  # a second session
    assert (await client.get("/probe/principal", headers=bearer(token))).status_code == 200

    r = await client.post(f"{USERS}/{user.id}/deactivate", headers=bearer(admin_token))
    assert r.status_code == 200
    assert r.json()["status"] == "inactive" and r.json()["id"] == str(user.id)

    blocked = await client.get("/probe/principal", headers=bearer(token))
    assert (blocked.status_code, code(blocked)) == (401, "INVALID_TOKEN")
    for raw_token in (raw, other_raw):
        gone = await client.post(REFRESH, json={"refresh_token": raw_token})
        assert (gone.status_code, code(gone)) == (401, "INVALID_REFRESH_TOKEN")
    rows = (
        (await db_session.execute(select(RefreshToken).where(RefreshToken.user_id == user.id)))
        .scalars()
        .all()
    )
    assert rows and all(row.revoked_at is not None for row in rows)
    audit = (
        await db_session.execute(select(AuditLog).where(AuditLog.action == "user.deactivated"))
    ).scalar_one()
    assert audit.actor_user_id == admin.id and audit.subject_id == user.id
    assert family is not None


async def test_deactivate_trainer_hides_tenant_404_for_coach_and_public(
    client: AsyncClient,
    admin_token: str,
    make_trainer: MakeUser,
    make_coach: MakeCoach,
    add_share_link: AddShareLink,
    principal_token: PrincipalToken,
    db_session: AsyncSession,
) -> None:
    trainer = await make_trainer()
    profile = await db_session.get(TrainerProfile, trainer.id)
    assert profile is not None
    coach = await make_coach(trainer)
    link = await add_share_link(trainer)
    coach_headers = bearer(principal_token(coach.user, trainer_id=trainer.id))
    assert (await client.get("/probe/principal", headers=coach_headers)).status_code == 200
    assert (await client.get(f"/api/v1/public/share-links/{link.code}")).status_code == 200
    slug_url = f"/api/v1/public/trainers/{profile.slug}/branding"
    assert (await client.get(slug_url)).status_code == 200

    r = await client.post(f"{USERS}/{trainer.id}/deactivate", headers=bearer(admin_token))
    assert r.status_code == 200

    hidden = await client.get("/probe/principal", headers=coach_headers)
    assert (hidden.status_code, code(hidden)) == (404, "NOT_FOUND")
    preview = await client.get(f"/api/v1/public/share-links/{link.code}")
    assert (preview.status_code, code(preview)) == (404, "SHARE_LINK_INVALID")
    assert (await client.get(slug_url)).status_code == 404


async def test_deactivate_idempotent_already_inactive_200(
    client: AsyncClient, admin_token: str, make_user: MakeUser, db_session: AsyncSession
) -> None:
    user = await make_user(status=UserStatus.inactive)
    for _ in range(2):  # no self-guard, no error on repeat
        r = await client.post(f"{USERS}/{user.id}/deactivate", headers=bearer(admin_token))
        assert (r.status_code, r.json()["status"]) == (200, "inactive")
    audits = (
        (await db_session.execute(select(AuditLog).where(AuditLog.action == "user.deactivated")))
        .scalars()
        .all()
    )
    assert audits == []  # nothing changed, nothing audited


async def test_admin_can_deactivate_themselves_no_self_guard(
    client: AsyncClient, admin_token: str, admin: Any
) -> None:
    r = await client.post(f"{USERS}/{admin.id}/deactivate", headers=bearer(admin_token))
    assert (r.status_code, r.json()["status"]) == (200, "inactive")


async def test_reactivate_transitions_and_pending_409_invalid_status_transition(
    client: AsyncClient,
    admin_token: str,
    admin: Any,
    make_user: MakeUser,
    principal_token: PrincipalToken,
    db_session: AsyncSession,
) -> None:
    headers = bearer(admin_token)
    inactive = await make_user(status=UserStatus.inactive)
    r = await client.post(f"{USERS}/{inactive.id}/reactivate", headers=headers)
    assert (r.status_code, r.json()["status"]) == (200, "active")
    assert (
        await client.get("/probe/principal", headers=bearer(principal_token(inactive)))
    ).status_code == 200
    audit = (
        await db_session.execute(select(AuditLog).where(AuditLog.action == "user.reactivated"))
    ).scalar_one()
    assert audit.actor_user_id == admin.id and audit.subject_id == inactive.id

    again = await client.post(f"{USERS}/{inactive.id}/reactivate", headers=headers)
    assert (again.status_code, again.json()["status"]) == (200, "active")  # already active

    pending = await make_user(status=UserStatus.pending)
    for action in ("reactivate", "deactivate"):
        r = await client.post(f"{USERS}/{pending.id}/{action}", headers=headers)
        assert (r.status_code, code(r)) == (409, "INVALID_STATUS_TRANSITION"), action
    await db_session.refresh(pending)
    assert pending.status is UserStatus.pending  # only password/setup activates

    unknown = "00000000-0000-7000-8000-000000000000"
    for action in ("reactivate", "deactivate"):
        r = await client.post(f"{USERS}/{unknown}/{action}", headers=headers)
        assert (r.status_code, code(r)) == (404, "NOT_FOUND")
