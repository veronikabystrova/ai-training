from typing import Any
from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.identity import (
    ApprovalStatus,
    LinkStatus,
    PlayerProfile,
    PurchaseApproval,
    User,
)
from tests.api.helpers import bearer
from tests.factories import (
    LinkPlayer,
    MakeApproval,
    MakeDependent,
    MakeTrainer,
    MakeUser,
    PrincipalToken,
)
from tests.fakes import FakeJobQueue, FakePurchaseExecutor

APPROVALS = "/api/v1/me/approvals"


def code(response: Any) -> str:
    return str(response.json()["error"]["code"])


async def setup_pending(
    make_user: MakeUser,
    make_dependent: MakeDependent,
    make_trainer: MakeTrainer,
    link_player: LinkPlayer,
    make_approval: MakeApproval,
) -> tuple[User, User, PlayerProfile, PurchaseApproval]:
    parent = await make_user()
    trainer = await make_trainer()
    child = await make_dependent(parent=parent)
    await link_player(trainer, child)
    approval = await make_approval(parent=parent, trainer=trainer, player=child)
    return parent, trainer, child, approval


async def test_list_requires_trainer_id_and_valid_context_404(
    client: AsyncClient,
    make_user: MakeUser,
    make_dependent: MakeDependent,
    make_trainer: MakeTrainer,
    link_player: LinkPlayer,
    make_approval: MakeApproval,
    principal_token: PrincipalToken,
) -> None:
    parent, trainer, _, _ = await setup_pending(
        make_user, make_dependent, make_trainer, link_player, make_approval
    )
    headers = bearer(principal_token(parent))

    assert (await client.get(APPROVALS, headers=headers)).status_code == 422
    unknown = await client.get(APPROVALS, params={"trainer_id": str(uuid4())}, headers=headers)
    assert (unknown.status_code, code(unknown)) == (404, "NOT_FOUND")
    ok = await client.get(APPROVALS, params={"trainer_id": str(trainer.id)}, headers=headers)
    assert ok.status_code == 200


async def test_list_only_callers_dependents_approvals_filtered_by_status(
    client: AsyncClient,
    make_user: MakeUser,
    make_dependent: MakeDependent,
    make_trainer: MakeTrainer,
    link_player: LinkPlayer,
    make_approval: MakeApproval,
    principal_token: PrincipalToken,
    db_session: AsyncSession,
) -> None:
    parent, trainer, child, pending = await setup_pending(
        make_user, make_dependent, make_trainer, link_player, make_approval
    )
    decided = await make_approval(parent=parent, trainer=trainer, player=child)
    decided.status = ApprovalStatus.denied
    other_parent = await make_user()
    other_child = await make_dependent(parent=other_parent)
    await link_player(trainer, other_child)
    await make_approval(parent=other_parent, trainer=trainer, player=other_child)
    await db_session.flush()
    params = {"trainer_id": str(trainer.id)}
    headers = bearer(principal_token(parent))

    everything = await client.get(APPROVALS, params=params, headers=headers)
    assert {i["id"] for i in everything.json()["items"]} == {str(pending.id), str(decided.id)}
    only_pending = await client.get(
        APPROVALS, params={**params, "status": "pending"}, headers=headers
    )
    assert [i["id"] for i in only_pending.json()["items"]] == [str(pending.id)]
    item = only_pending.json()["items"][0]
    assert (item["kind"], item["currency"], item["amount"]) == ("purchase", "USD", "12.50")

    first_page = await client.get(APPROVALS, params={**params, "limit": 1}, headers=headers)
    cursor = first_page.json()["next_cursor"]
    assert cursor is not None
    second = await client.get(
        APPROVALS, params={**params, "limit": 1, "cursor": cursor}, headers=headers
    )
    assert second.status_code == 200
    assert {first_page.json()["items"][0]["id"], second.json()["items"][0]["id"]} == {
        str(pending.id),
        str(decided.id),
    }
    assert second.json()["next_cursor"] is None


async def test_list_child_and_trainer_403(
    client: AsyncClient,
    make_user: MakeUser,
    make_dependent: MakeDependent,
    make_trainer: MakeTrainer,
    principal_token: PrincipalToken,
    db_session: AsyncSession,
) -> None:
    trainer = await make_trainer()
    profile = await make_dependent(with_login=True)
    child = await db_session.get(User, profile.login_user_id)
    assert child is not None
    params = {"trainer_id": str(trainer.id)}
    for token in (principal_token(child, child=True), principal_token(trainer)):
        r = await client.get(APPROVALS, params=params, headers=bearer(token))
        assert r.status_code == 403


async def test_approve_executes_once_and_returns_approved(
    client: AsyncClient,
    executor: FakePurchaseExecutor,
    make_user: MakeUser,
    make_dependent: MakeDependent,
    make_trainer: MakeTrainer,
    link_player: LinkPlayer,
    make_approval: MakeApproval,
    principal_token: PrincipalToken,
) -> None:
    parent, _, _, approval = await setup_pending(
        make_user, make_dependent, make_trainer, link_player, make_approval
    )
    headers = {**bearer(principal_token(parent)), "Idempotency-Key": "k1"}

    r = await client.post(f"{APPROVALS}/{approval.id}/approve", headers=headers)

    assert r.status_code == 200
    assert r.json()["status"] == "approved"
    assert r.json()["decided_at"] is not None
    assert len(executor.executed) == 1


async def test_approve_replay_same_key_returns_stored_response_no_second_effect(
    client: AsyncClient,
    executor: FakePurchaseExecutor,
    make_user: MakeUser,
    make_dependent: MakeDependent,
    make_trainer: MakeTrainer,
    link_player: LinkPlayer,
    make_approval: MakeApproval,
    principal_token: PrincipalToken,
) -> None:
    parent, _, _, approval = await setup_pending(
        make_user, make_dependent, make_trainer, link_player, make_approval
    )
    headers = {**bearer(principal_token(parent)), "Idempotency-Key": "k1"}
    url = f"{APPROVALS}/{approval.id}/approve"

    first = await client.post(url, headers=headers)
    replay = await client.post(url, headers=headers)

    assert replay.status_code == 200
    assert replay.json() == first.json()
    assert len(executor.executed) == 1
    new_key = await client.post(url, headers={**headers, "Idempotency-Key": "k2"})
    assert (new_key.status_code, code(new_key)) == (409, "APPROVAL_NOT_PENDING")


async def test_approve_same_key_different_body_409_reused(
    client: AsyncClient,
    make_user: MakeUser,
    make_dependent: MakeDependent,
    make_trainer: MakeTrainer,
    link_player: LinkPlayer,
    make_approval: MakeApproval,
    principal_token: PrincipalToken,
) -> None:
    parent, _, _, approval = await setup_pending(
        make_user, make_dependent, make_trainer, link_player, make_approval
    )
    headers = {**bearer(principal_token(parent)), "Idempotency-Key": "k1"}
    url = f"{APPROVALS}/{approval.id}/deny"

    first = await client.post(url, json={"note": "no"}, headers=headers)
    other = await client.post(url, json={"note": "different"}, headers=headers)

    assert first.status_code == 200
    assert (other.status_code, code(other)) == (409, "IDEMPOTENCY_KEY_REUSED")


async def test_approve_missing_key_400(
    client: AsyncClient,
    make_user: MakeUser,
    make_dependent: MakeDependent,
    make_trainer: MakeTrainer,
    link_player: LinkPlayer,
    make_approval: MakeApproval,
    principal_token: PrincipalToken,
) -> None:
    parent, _, _, approval = await setup_pending(
        make_user, make_dependent, make_trainer, link_player, make_approval
    )
    r = await client.post(
        f"{APPROVALS}/{approval.id}/approve", headers=bearer(principal_token(parent))
    )
    assert (r.status_code, code(r)) == (400, "IDEMPOTENCY_KEY_REQUIRED")


async def test_approve_not_pending_409(
    client: AsyncClient,
    executor: FakePurchaseExecutor,
    clock: Any,
    make_user: MakeUser,
    make_dependent: MakeDependent,
    make_trainer: MakeTrainer,
    link_player: LinkPlayer,
    make_approval: MakeApproval,
    principal_token: PrincipalToken,
    db_session: AsyncSession,
) -> None:
    parent, trainer, child, decided = await setup_pending(
        make_user, make_dependent, make_trainer, link_player, make_approval
    )
    decided.status = ApprovalStatus.denied
    expired = await make_approval(parent=parent, trainer=trainer, player=child)
    expired.status = ApprovalStatus.expired
    stale = await make_approval(parent=parent, trainer=trainer, player=child)  # pending, past due
    stale.expires_at = clock.now()
    await db_session.flush()
    token = bearer(principal_token(parent))

    for n, row in enumerate((decided, expired, stale)):
        r = await client.post(
            f"{APPROVALS}/{row.id}/approve", headers={**token, "Idempotency-Key": f"k{n}"}
        )
        assert (r.status_code, code(r)) == (409, "APPROVAL_NOT_PENDING")
    assert executor.executed == []


async def test_approve_other_parents_approval_404(
    client: AsyncClient,
    executor: FakePurchaseExecutor,
    make_user: MakeUser,
    make_dependent: MakeDependent,
    make_trainer: MakeTrainer,
    link_player: LinkPlayer,
    make_approval: MakeApproval,
    principal_token: PrincipalToken,
) -> None:
    _, _, _, approval = await setup_pending(
        make_user, make_dependent, make_trainer, link_player, make_approval
    )
    stranger = await make_user()
    headers = {**bearer(principal_token(stranger)), "Idempotency-Key": "k1"}

    r = await client.post(f"{APPROVALS}/{approval.id}/approve", headers=headers)

    assert (r.status_code, code(r)) == (404, "NOT_FOUND")
    assert executor.executed == []
    assert (await client.post(f"{APPROVALS}/{uuid4()}/approve", headers=headers)).status_code == 404


async def test_decision_forbidden_for_child_and_trainer(
    client: AsyncClient,
    make_user: MakeUser,
    make_dependent: MakeDependent,
    make_trainer: MakeTrainer,
    link_player: LinkPlayer,
    make_approval: MakeApproval,
    principal_token: PrincipalToken,
    db_session: AsyncSession,
) -> None:
    _, trainer, _, approval = await setup_pending(
        make_user, make_dependent, make_trainer, link_player, make_approval
    )
    login_profile = await make_dependent(with_login=True)
    login = await db_session.get(User, login_profile.login_user_id)
    assert login is not None
    for token in (principal_token(login, child=True), principal_token(trainer)):
        r = await client.post(
            f"{APPROVALS}/{approval.id}/approve",
            headers={**bearer(token), "Idempotency-Key": "k1"},
        )
        assert r.status_code == 403


async def test_executor_failure_rolls_back_decision(
    client: AsyncClient,
    executor: FakePurchaseExecutor,
    make_user: MakeUser,
    make_dependent: MakeDependent,
    make_trainer: MakeTrainer,
    link_player: LinkPlayer,
    make_approval: MakeApproval,
    principal_token: PrincipalToken,
    db_session: AsyncSession,
) -> None:
    parent, _, _, approval = await setup_pending(
        make_user, make_dependent, make_trainer, link_player, make_approval
    )
    executor.error = ValueError("card declined")
    headers = {**bearer(principal_token(parent)), "Idempotency-Key": "k1"}
    url = f"{APPROVALS}/{approval.id}/approve"

    failed = await client.post(url, headers=headers)
    assert failed.status_code == 500
    await db_session.refresh(approval)
    assert approval.status is ApprovalStatus.pending
    assert approval.responded_at is None

    executor.error = None  # the same key may be retried: nothing was stored for it
    retry = await client.post(url, headers=headers)
    assert retry.status_code == 200
    assert len(executor.executed) == 1


async def test_deny_records_note_and_enqueues_child_notification(
    client: AsyncClient,
    executor: FakePurchaseExecutor,
    jobs: FakeJobQueue,
    make_user: MakeUser,
    make_dependent: MakeDependent,
    make_trainer: MakeTrainer,
    link_player: LinkPlayer,
    make_approval: MakeApproval,
    principal_token: PrincipalToken,
) -> None:
    parent, _, _, approval = await setup_pending(
        make_user, make_dependent, make_trainer, link_player, make_approval
    )
    headers = {**bearer(principal_token(parent)), "Idempotency-Key": "k1"}
    url = f"{APPROVALS}/{approval.id}/deny"

    r = await client.post(url, json={"note": "Too expensive"}, headers=headers)
    replay = await client.post(url, json={"note": "Too expensive"}, headers=headers)

    assert r.status_code == 200
    assert (r.json()["status"], r.json()["decision_note"]) == ("denied", "Too expensive")
    assert replay.json() == r.json()
    assert executor.executed == []
    assert [(j.name, j.kwargs) for j in jobs.enqueued] == [
        ("notify_child_decision", {"approval_id": str(approval.id)})
    ]


async def test_approve_404_when_dependent_removed_or_link_ended_deny_still_allowed(
    client: AsyncClient,
    executor: FakePurchaseExecutor,
    make_user: MakeUser,
    make_dependent: MakeDependent,
    make_trainer: MakeTrainer,
    link_player: LinkPlayer,
    make_approval: MakeApproval,
    principal_token: PrincipalToken,
    db_session: AsyncSession,
) -> None:
    parent = await make_user()
    trainer = await make_trainer()
    removed = await make_dependent(parent=parent)
    unlinked = await make_dependent(parent=parent)
    await link_player(trainer, removed)
    link = await link_player(trainer, unlinked)
    approval_removed = await make_approval(parent=parent, trainer=trainer, player=removed)
    approval_unlinked = await make_approval(parent=parent, trainer=trainer, player=unlinked)
    removed.is_active = False
    link.status = LinkStatus.inactive
    await db_session.flush()
    base = bearer(principal_token(parent))

    for n, approval in enumerate((approval_removed, approval_unlinked)):
        headers = {**base, "Idempotency-Key": f"k{n}"}
        r = await client.post(f"{APPROVALS}/{approval.id}/approve", headers=headers)
        assert (r.status_code, code(r)) == (404, "NOT_FOUND")
        await db_session.refresh(approval)
        assert approval.status is ApprovalStatus.pending
    assert executor.executed == []

    denied = await client.post(
        f"{APPROVALS}/{approval_removed.id}/deny", json={}, headers={**base, "Idempotency-Key": "d"}
    )
    assert (denied.status_code, denied.json()["status"]) == (200, "denied")


async def test_deny_without_body_ok_and_unknown_field_422(
    client: AsyncClient,
    make_user: MakeUser,
    make_dependent: MakeDependent,
    make_trainer: MakeTrainer,
    link_player: LinkPlayer,
    make_approval: MakeApproval,
    principal_token: PrincipalToken,
) -> None:
    parent, trainer, child, approval = await setup_pending(
        make_user, make_dependent, make_trainer, link_player, make_approval
    )
    other = await make_approval(parent=parent, trainer=trainer, player=child)
    base = bearer(principal_token(parent))

    extra = await client.post(
        f"{APPROVALS}/{approval.id}/deny",
        json={"note": "x", "extra": 1},
        headers={**base, "Idempotency-Key": "e"},
    )
    assert (extra.status_code, code(extra)) == (422, "VALIDATION_ERROR")

    bare = await client.post(
        f"{APPROVALS}/{other.id}/deny", headers={**base, "Idempotency-Key": "b"}
    )
    assert (bare.status_code, bare.json()["status"], bare.json()["decision_note"]) == (
        200,
        "denied",
        None,
    )


async def test_tokens_approval_reports_token_amount_and_currency(
    client: AsyncClient,
    make_user: MakeUser,
    make_dependent: MakeDependent,
    make_trainer: MakeTrainer,
    link_player: LinkPlayer,
    make_approval: MakeApproval,
    principal_token: PrincipalToken,
) -> None:
    parent = await make_user()
    trainer = await make_trainer()
    child = await make_dependent(parent=parent)
    await link_player(trainer, child)
    await make_approval(parent=parent, trainer=trainer, player=child, token_amount=40)

    r = await client.get(
        APPROVALS, params={"trainer_id": str(trainer.id)}, headers=bearer(principal_token(parent))
    )

    item = r.json()["items"][0]
    assert (item["currency"], float(item["amount"])) == ("TOKENS", 40.0)
