from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.identity import PurchaseApproval
from app.repositories.audit import AuditRepository
from app.services.audit_service import AuditService, DbAuditService
from app.services.feature_flag_service import FeatureFlagService, StaticFeatureFlagService
from app.services.ports import CampSubmissionReader, CommitmentChecker, PurchaseExecutor
from tests.fakes import FakeCampSubmissionReader, FakeCommitmentChecker, FakePurchaseExecutor


async def test_commitment_checker_fake_records_and_can_block_or_fail() -> None:
    profile, trainer, rsvp = uuid4(), uuid4(), uuid4()
    fake = FakeCommitmentChecker(rsvps=[rsvp])
    checker: CommitmentChecker = fake
    assert await checker.future_paid_rsvps(profile, trainer) == [rsvp]
    assert fake.calls == [(profile, trainer)]
    fake.error = RuntimeError("down")
    with pytest.raises(RuntimeError, match="down"):
        await checker.future_paid_rsvps(profile, trainer)


async def test_purchase_executor_fake_records_and_can_fail() -> None:
    approval = PurchaseApproval()
    fake = FakePurchaseExecutor()
    executor: PurchaseExecutor = fake
    await executor.execute(approval)
    assert fake.executed == [approval]
    fake.error = RuntimeError("declined")
    with pytest.raises(RuntimeError, match="declined"):
        await executor.execute(approval)
    assert fake.executed == [approval]


async def test_camp_submission_reader_fake() -> None:
    known = uuid4()
    fake = FakeCampSubmissionReader({known: {"first_name": "A"}})
    reader: CampSubmissionReader = fake
    assert await reader.get_prefill(known) == {"first_name": "A"}
    assert await reader.get_prefill(uuid4()) is None
    assert len(fake.requested) == 2
    fake.error = RuntimeError("unavailable")
    with pytest.raises(RuntimeError, match="unavailable"):
        await reader.get_prefill(known)


async def test_db_audit_service_appends_a_row(db_session: AsyncSession) -> None:
    service: AuditService = DbAuditService(AuditRepository(db_session))
    actor, act = uuid4(), uuid4()
    await service.record(
        action="impersonated_request", actor_id=actor, act=act, method="GET", path="/x"
    )
    row = (
        await db_session.execute(
            text("select action, actor_user_id, act_user_id, method, path from audit_log")
        )
    ).one()
    assert tuple(row) == ("impersonated_request", actor, act, "GET", "/x")


async def test_static_feature_flags_default_off() -> None:
    flags: FeatureFlagService = StaticFeatureFlagService({"on": True})
    assert await flags.is_enabled("on") is True
    assert await flags.is_enabled("unknown", uuid4()) is False
