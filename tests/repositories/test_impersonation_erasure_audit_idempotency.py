from datetime import timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import new_id
from app.models.identity import UserRole
from app.repositories.audit import AuditRepository
from app.repositories.erasure import UserErasureRepository
from app.repositories.idempotency import IdempotencyRepository
from app.repositories.impersonation import ImpersonationSessionRepository
from tests.factories import MakeUser
from tests.fakes import FakeClock

JTI_1 = new_id()
JTI_2 = new_id()


async def test_impersonation_open_close_expire(
    db_session: AsyncSession, make_user: MakeUser, clock: FakeClock
) -> None:
    repo = ImpersonationSessionRepository(db_session)
    admin = await make_user(role=UserRole.super_admin)
    target = await make_user()
    s1 = await repo.open(
        admin_user_id=admin.id,
        target_user_id=target.id,
        jti=JTI_1,
        expires_at=clock.now() + timedelta(minutes=30),
        reason="support",
    )
    s2 = await repo.open(
        admin_user_id=admin.id,
        target_user_id=target.id,
        jti=JTI_2,
        expires_at=clock.now() - timedelta(minutes=1),
        reason=None,
    )
    found = await repo.get_by_jti(JTI_1)
    assert found is not None and found.id == s1.id
    assert await repo.get_by_jti(new_id()) is None

    assert await repo.close(JTI_1, now=clock.now()) is True
    assert await repo.close(JTI_1, now=clock.now()) is False
    assert await repo.expire_due(now=clock.now()) == [s2.id]
    assert await repo.expire_due(now=clock.now()) == []


async def test_erasure_ledger_append(
    db_session: AsyncSession, make_user: MakeUser, clock: FakeClock
) -> None:
    admin = await make_user(role=UserRole.super_admin)
    target = await make_user()
    row = await UserErasureRepository(db_session).append(
        original_user_id=target.id,
        original_email_hash="a" * 64,
        erased_by_user_id=admin.id,
        reason="gdpr",
        legal_retention_until=clock.now() + timedelta(days=365),
    )
    assert row.original_user_id == target.id and row.id is not None


async def test_audit_append(db_session: AsyncSession, make_user: MakeUser) -> None:
    actor = await make_user()
    row = await AuditRepository(db_session).append(
        actor_user_id=actor.id,
        action="user.status_changed",
        subject_type="user",
        subject_id=actor.id,
        method="PATCH",
        path="/admin/users/x",
        metadata={"status": "inactive"},
    )
    assert row.metadata_ == {"status": "inactive"} and row.act_user_id is None


async def test_idempotency_reserve_store_and_purge(
    db_session: AsyncSession, make_user: MakeUser, clock: FakeClock
) -> None:
    repo = IdempotencyRepository(db_session)
    u = await make_user()
    kwargs = dict(
        key="k1",
        principal_id=u.id,
        endpoint="POST /x",
        request_hash="h" * 64,
        expires_at=clock.now() + timedelta(hours=24),
    )
    assert await repo.reserve(**kwargs) is True  # type: ignore[arg-type]
    assert await repo.reserve(**kwargs) is False  # type: ignore[arg-type]
    pending = await repo.get(key="k1", principal_id=u.id)
    assert pending is not None and pending.response_status is None

    await repo.store_response(
        key="k1", principal_id=u.id, response_status=201, response_body={"id": "x"}
    )
    stored = await repo.get(key="k1", principal_id=u.id)
    assert stored is not None
    assert (stored.response_status, stored.response_body) == (201, {"id": "x"})
    assert await repo.get(key="k1", principal_id=UUID(int=0)) is None
    assert await repo.purge(before=clock.now() + timedelta(days=2)) == 1
