from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.identity import (
    CoachProfile,
    LinkSource,
    LinkStatus,
    PlayerProfile,
    ShareLinkEvent,
    TrainerPlayerLink,
    User,
    UserRole,
    UserStatus,
)
from app.repositories.coach_profile import CoachProfileRepository
from app.repositories.user import UserRepository
from tests.api.auth.helpers import code, login
from tests.api.helpers import bearer
from tests.factories import (
    AddShareLink,
    MakeCoach,
    MakeDependent,
    MakePlayer,
    MakeTrainer,
    MakeUser,
    PrincipalToken,
)
from tests.fakes import FakeClock, FakeJobQueue

REGISTER = "/api/v1/auth/register"
STRONG = "Tr1cky-Purple-Elephant-42!"


def body(email: str = "new@x.com", **extra: Any) -> dict[str, Any]:
    return {
        "email": email,
        "password": STRONG,
        "first_name": "Ada",
        "last_name": "Lovelace",
        **extra,
    }


def url(share_code: str) -> str:
    return f"{REGISTER}?share_code={share_code}"


async def _user(db: AsyncSession, email: str) -> User:
    user = await UserRepository(db).get_by_email(email)
    assert user is not None
    return user


async def _links_of(db: AsyncSession, profile_id: Any) -> list[TrainerPlayerLink]:
    rows = await db.execute(
        select(TrainerPlayerLink).where(TrainerPlayerLink.player_profile_id == profile_id)
    )
    return list(rows.scalars())


# --- static player link, new account -------------------------------------------------------


async def test_static_link_registers_player_with_profile_and_link(
    client: AsyncClient,
    db_session: AsyncSession,
    make_trainer: MakeTrainer,
    add_share_link: AddShareLink,
    jobs: FakeJobQueue,
) -> None:
    trainer = await make_trainer()
    link = await add_share_link(trainer)

    r = await client.post(url(link.code), json=body(phone="+33123456789", timezone="Europe/Paris"))
    assert r.status_code == 201
    data = r.json()
    assert data["user"]["role"] == "player"
    assert data["user"]["email"] == "new@x.com"
    assert data["user"]["email_verified"] is False
    assert data["user"]["trainer_id"] is None
    assert data["user"]["child"] is False
    assert "password_hash" not in r.text
    assert STRONG not in r.text

    user = await _user(db_session, "new@x.com")
    assert user.status is UserStatus.active
    assert user.password_hash != STRONG
    profile = (
        await db_session.execute(
            select(PlayerProfile).where(PlayerProfile.owner_user_id == user.id)
        )
    ).scalar_one()
    assert profile.is_dependent is False
    (tpl,) = await _links_of(db_session, profile.id)
    assert tpl.trainer_id == trainer.id
    assert tpl.source is LinkSource.share_link
    assert tpl.share_link_id == link.id
    assert tpl.status is LinkStatus.active
    await db_session.refresh(link)
    assert link.use_count == 1
    events = (
        await db_session.execute(
            select(ShareLinkEvent).where(ShareLinkEvent.share_link_id == link.id)
        )
    ).scalars()
    assert [e.kind for e in events] == ["registered"]

    # The issued access token authenticates and the context is reachable.
    me = await client.get("/probe/principal", headers=bearer(data["tokens"]["access_token"]))
    assert me.status_code == 200
    assert [j.name for j in jobs.enqueued] == ["send_welcome_email", "send_verification_email"]
    assert jobs.enqueued[0].kwargs == {"user_id": str(user.id)}
    assert len(jobs.enqueued[1].kwargs["token"]) >= 20


async def test_static_link_unlimited_uses_when_max_uses_is_null(
    client: AsyncClient,
    db_session: AsyncSession,
    make_trainer: MakeTrainer,
    add_share_link: AddShareLink,
) -> None:
    link = await add_share_link(await make_trainer())
    for i in range(3):
        assert (await client.post(url(link.code), json=body(f"p{i}@x.com"))).status_code == 201
    await db_session.refresh(link)
    assert link.use_count == 3


async def test_registered_email_is_case_insensitive_unique(
    client: AsyncClient, make_trainer: MakeTrainer, add_share_link: AddShareLink
) -> None:
    link = await add_share_link(await make_trainer())
    assert (await client.post(url(link.code), json=body("Mixed@x.com"))).status_code == 201
    r = await client.post(url(link.code), json=body("mixed@X.com"))
    assert r.status_code == 409
    assert code(r) == "ACCOUNT_EXISTS"


async def test_weak_password_is_422_and_does_not_consume_the_link(
    client: AsyncClient,
    db_session: AsyncSession,
    make_trainer: MakeTrainer,
    add_share_link: AddShareLink,
) -> None:
    link = await add_share_link(await make_trainer(), max_uses=1)
    weak = {**body(), "password": "password1234"}
    r = await client.post(url(link.code), json=weak)
    assert r.status_code == 422
    assert code(r) == "WEAK_PASSWORD"
    await db_session.refresh(link)
    assert link.use_count == 0
    assert (await client.post(url(link.code), json=body())).status_code == 201


async def test_duplicate_email_409_account_exists_with_next_hint(
    client: AsyncClient,
    db_session: AsyncSession,
    make_trainer: MakeTrainer,
    make_user: MakeUser,
    add_share_link: AddShareLink,
) -> None:
    link = await add_share_link(await make_trainer())
    await make_user(email="taken@x.com")
    r = await client.post(url(link.code), json=body("taken@x.com"))
    assert r.status_code == 409
    assert r.json()["error"] == {
        "code": "ACCOUNT_EXISTS",
        "message": "An account with this email exists",
        "details": [{"next": "login_then_redeem"}],
    }
    await db_session.refresh(link)
    assert link.use_count == 0


# --- request validation --------------------------------------------------------------------


async def test_exactly_one_of_share_code_or_camp_submission(
    client: AsyncClient, make_trainer: MakeTrainer, add_share_link: AddShareLink
) -> None:
    neither = await client.post(REGISTER, json=body())
    assert neither.status_code == 422
    assert code(neither) == "VALIDATION_ERROR"
    both = await client.post(f"{REGISTER}?share_code=a&camp_submission_id={uuid4()}", json=body())
    assert both.status_code == 422
    bad_uuid = await client.post(f"{REGISTER}?camp_submission_id=nope", json=body())
    assert bad_uuid.status_code == 422


async def test_camp_submission_is_stubbed_until_epic_08(client: AsyncClient) -> None:
    r = await client.post(f"{REGISTER}?camp_submission_id={uuid4()}", json=body())
    assert r.status_code == 404
    assert code(r) == "NOT_FOUND"


async def test_empty_player_profile_ids_422(
    client: AsyncClient, make_trainer: MakeTrainer, add_share_link: AddShareLink
) -> None:
    link = await add_share_link(await make_trainer())
    r = await client.post(url(link.code), json=body(player_profile_ids=[]))
    assert r.status_code == 422


@pytest.mark.parametrize(
    "extra",
    [
        {"unknown": 1},
        {"timezone": "Mars/Base"},
        {"first_name": ""},
        {"phone": "1" * 33},
        {"password": "short"},
    ],
)
async def test_invalid_bodies_are_422(
    client: AsyncClient,
    make_trainer: MakeTrainer,
    add_share_link: AddShareLink,
    extra: dict[str, Any],
) -> None:
    link = await add_share_link(await make_trainer())
    r = await client.post(url(link.code), json={**body(), **extra})
    assert r.status_code == 422
    assert code(r) == "VALIDATION_ERROR"


# --- invalid links -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "state", ["unknown", "inactive", "expired", "exhausted", "trainer_deactivated"]
)
async def test_invalid_link_states_all_404_share_link_invalid(
    client: AsyncClient,
    db_session: AsyncSession,
    state: str,
    make_trainer: MakeTrainer,
    add_share_link: AddShareLink,
    clock: FakeClock,
) -> None:
    trainer = await make_trainer()
    code_value = "does-not-exist"
    if state != "unknown":
        link = await add_share_link(
            trainer,
            max_uses=1 if state == "exhausted" else None,
            expires_at=clock.now() - timedelta(seconds=1) if state == "expired" else None,
        )
        code_value = link.code
        if state == "inactive":
            link.is_active = False
        elif state == "exhausted":
            link.use_count = 1
        elif state == "trainer_deactivated":
            trainer.status = UserStatus.inactive
        await db_session.flush()
    r = await client.post(url(code_value), json=body())
    assert r.status_code == 404
    assert r.json() == {
        "error": {
            "code": "SHARE_LINK_INVALID",
            "message": "Share link is invalid or expired",
            "details": [],
        }
    }
    assert await UserRepository(db_session).get_by_email("new@x.com") is None


# --- coach_unique link ---------------------------------------------------------------------


async def test_coach_link_creates_coach_with_active_tenure(
    client: AsyncClient,
    db_session: AsyncSession,
    make_trainer: MakeTrainer,
    add_share_link: AddShareLink,
    jobs: FakeJobQueue,
) -> None:
    trainer = await make_trainer()
    link = await add_share_link(trainer, kind="coach_unique", target_email="Coach@x.com")
    r = await client.post(url(link.code), json=body("coach@X.com"))
    assert r.status_code == 201
    data = r.json()
    assert data["user"]["role"] == "coach"
    assert data["user"]["trainer_id"] == str(trainer.id)

    coach = await _user(db_session, "coach@x.com")
    tenure = await CoachProfileRepository(db_session).get_active(
        trainer_id=trainer.id, user_id=coach.id
    )
    assert tenure is not None
    assert (
        await db_session.execute(
            select(PlayerProfile).where(PlayerProfile.owner_user_id == coach.id)
        )
    ).first() is None
    await db_session.refresh(link)
    assert link.use_count == 1
    assert [j.name for j in jobs.enqueued] == ["send_welcome_email", "send_verification_email"]
    # The coach token resolves the tenant on a protected route.
    me = await client.get("/probe/principal", headers=bearer(data["tokens"]["access_token"]))
    assert me.status_code == 200
    assert me.json()["trainer_id"] == str(trainer.id)


async def test_coach_link_email_mismatch_403(
    client: AsyncClient,
    db_session: AsyncSession,
    make_trainer: MakeTrainer,
    add_share_link: AddShareLink,
) -> None:
    link = await add_share_link(await make_trainer(), kind="coach_unique", target_email="c@x.com")
    r = await client.post(url(link.code), json=body("other@x.com"))
    assert r.status_code == 403
    assert code(r) == "LINK_EMAIL_MISMATCH"
    await db_session.refresh(link)
    assert link.use_count == 0


async def test_coach_link_existing_account_409_wrong_link_kind(
    client: AsyncClient,
    make_trainer: MakeTrainer,
    make_user: MakeUser,
    add_share_link: AddShareLink,
) -> None:
    await make_user(email="c@x.com")
    link = await add_share_link(await make_trainer(), kind="coach_unique", target_email="c@x.com")
    r = await client.post(url(link.code), json=body("c@x.com"))
    assert r.status_code == 409
    assert code(r) == "WRONG_LINK_KIND"


async def test_coach_link_expiry_boundary_7_days(
    client: AsyncClient,
    make_trainer: MakeTrainer,
    add_share_link: AddShareLink,
    clock: FakeClock,
) -> None:
    trainer = await make_trainer()
    inside = await add_share_link(trainer, kind="coach_unique", target_email="a@x.com")
    outside = await add_share_link(trainer, kind="coach_unique", target_email="b@x.com")
    clock.advance(days=7, seconds=-1)
    assert (await client.post(url(inside.code), json=body("a@x.com"))).status_code == 201
    clock.advance(seconds=2)
    r = await client.post(url(outside.code), json=body("b@x.com"))
    assert r.status_code == 404
    assert code(r) == "SHARE_LINK_INVALID"


async def test_coach_link_is_single_use(
    client: AsyncClient, make_trainer: MakeTrainer, add_share_link: AddShareLink
) -> None:
    link = await add_share_link(await make_trainer(), kind="coach_unique", target_email="c@x.com")
    assert (await client.post(url(link.code), json=body("c@x.com"))).status_code == 201
    again = await client.post(url(link.code), json=body("c@x.com"))
    assert again.status_code == 404
    assert code(again) == "SHARE_LINK_INVALID"


# --- authenticated coach move --------------------------------------------------------------


async def test_authenticated_coach_with_closed_tenure_moves_to_new_trainer(
    client: AsyncClient,
    db_session: AsyncSession,
    make_trainer: MakeTrainer,
    make_coach: MakeCoach,
    make_user: MakeUser,
    add_share_link: AddShareLink,
    principal_token: PrincipalToken,
    clock: FakeClock,
) -> None:
    old, new = await make_trainer(), await make_trainer()
    coach = await make_user(role=UserRole.coach, email="c@x.com")
    await make_coach(old, user=coach)
    await CoachProfileRepository(db_session).close_tenure(
        trainer_id=old.id, user_id=coach.id, now=clock.now()
    )
    await db_session.flush()
    token = principal_token(coach, trainer_id=old.id)  # stale tid: issued before the tenure closed
    link = await add_share_link(new, kind="coach_unique", target_email="c@x.com")

    # Every other route keeps the 401 for a coach without an active tenure.
    assert (await client.get("/probe/principal", headers=bearer(token))).status_code == 401

    r = await client.post(url(link.code), json=body("ignored@x.com"), headers=bearer(token))
    assert r.status_code == 200
    data = r.json()
    assert data["linked_trainer_id"] == str(new.id)
    assert data["user"]["id"] == str(coach.id)
    assert data["user"]["trainer_id"] == str(new.id)
    assert "tokens" not in data
    active = await CoachProfileRepository(db_session).get_active_for_user(coach.id)
    assert active is not None
    assert active.trainer_id == new.id
    history = (
        await db_session.execute(select(CoachProfile).where(CoachProfile.user_id == coach.id))
    ).scalars()
    assert sorted(t.is_active for t in history) == [False, True]
    await db_session.refresh(link)
    assert link.use_count == 1
    # No new account was created.
    assert await UserRepository(db_session).get_by_email("ignored@x.com") is None


async def test_authenticated_coach_move_conflict_409_names_current_trainer(
    client: AsyncClient,
    make_trainer: MakeTrainer,
    make_coach: MakeCoach,
    make_user: MakeUser,
    add_share_link: AddShareLink,
    principal_token: PrincipalToken,
    db_session: AsyncSession,
) -> None:
    current, other = await make_trainer(), await make_trainer()
    coach = await make_user(role=UserRole.coach, email="c@x.com")
    await make_coach(current, user=coach)
    link = await add_share_link(other, kind="coach_unique", target_email="c@x.com")
    token = principal_token(coach, trainer_id=current.id)

    r = await client.post(url(link.code), json=body(), headers=bearer(token))
    assert r.status_code == 409
    assert code(r) == "COACH_ALREADY_ASSIGNED"
    assert r.json()["error"]["details"] == [{"current_trainer_id": str(current.id)}]
    await db_session.refresh(link)
    assert link.use_count == 0


async def test_authenticated_coach_with_other_email_is_refused(
    client: AsyncClient,
    make_trainer: MakeTrainer,
    make_user: MakeUser,
    add_share_link: AddShareLink,
    principal_token: PrincipalToken,
) -> None:
    coach = await make_user(role=UserRole.coach, email="c@x.com")
    link = await add_share_link(
        await make_trainer(), kind="coach_unique", target_email="someone-else@x.com"
    )
    r = await client.post(url(link.code), json=body(), headers=bearer(principal_token(coach)))
    assert r.status_code == 403
    assert code(r) == "LINK_EMAIL_MISMATCH"


async def test_any_tenure_principal_still_rejects_bad_tokens(
    client: AsyncClient, make_trainer: MakeTrainer, add_share_link: AddShareLink
) -> None:
    link = await add_share_link(await make_trainer())
    r = await client.post(url(link.code), json=body(), headers=bearer("junk"))
    assert r.status_code == 401
    assert code(r) == "INVALID_TOKEN"


# --- authenticated existing users ----------------------------------------------------------


async def test_authenticated_existing_player_redeem_links_only_200(
    client: AsyncClient,
    db_session: AsyncSession,
    make_trainer: MakeTrainer,
    make_player: MakePlayer,
    make_user: MakeUser,
    add_share_link: AddShareLink,
    principal_token: PrincipalToken,
    jobs: FakeJobQueue,
) -> None:
    owner = await make_user(email="p@x.com")
    profile = await make_player(owner=owner)
    trainer = await make_trainer()
    link = await add_share_link(trainer)

    r = await client.post(
        url(link.code), json=body("ignored@x.com"), headers=bearer(principal_token(owner))
    )
    assert r.status_code == 200
    assert r.json()["linked_trainer_id"] == str(trainer.id)
    assert r.json()["user"]["id"] == str(owner.id)
    (tpl,) = await _links_of(db_session, profile.id)
    assert (tpl.trainer_id, tpl.source, tpl.share_link_id) == (
        trainer.id,
        LinkSource.share_link,
        link.id,
    )
    await db_session.refresh(link)
    assert link.use_count == 1
    assert await UserRepository(db_session).get_by_email("ignored@x.com") is None
    assert jobs.enqueued == []  # no welcome mail for an existing account


async def test_authenticated_redeem_is_idempotent_for_an_active_link(
    client: AsyncClient,
    db_session: AsyncSession,
    make_trainer: MakeTrainer,
    make_player: MakePlayer,
    make_user: MakeUser,
    add_share_link: AddShareLink,
    principal_token: PrincipalToken,
) -> None:
    owner = await make_user()
    profile = await make_player(owner=owner)
    link = await add_share_link(await make_trainer())
    headers = bearer(principal_token(owner))
    for _ in range(2):
        assert (await client.post(url(link.code), json=body(), headers=headers)).status_code == 200
    assert len(await _links_of(db_session, profile.id)) == 1


async def test_parent_registers_existing_dependents_for_static_link(
    client: AsyncClient,
    db_session: AsyncSession,
    make_trainer: MakeTrainer,
    make_user: MakeUser,
    make_player: MakePlayer,
    make_dependent: MakeDependent,
    add_share_link: AddShareLink,
    principal_token: PrincipalToken,
) -> None:
    parent = await make_user(email="parent@x.com")
    own = await make_player(owner=parent)
    kid1 = await make_dependent(parent=parent)
    kid2 = await make_dependent(parent=parent)
    trainer = await make_trainer()
    link = await add_share_link(trainer)
    headers = bearer(principal_token(parent))

    r = await client.post(
        url(link.code),
        json=body(player_profile_ids=[str(kid1.id), str(kid1.id), str(kid2.id)]),
        headers=headers,
    )
    assert r.status_code == 200
    assert len(await _links_of(db_session, kid1.id)) == 1
    assert len(await _links_of(db_session, kid2.id)) == 1
    assert await _links_of(db_session, own.id) == []  # only the listed profiles are linked


async def test_parent_without_ids_links_own_profile_not_dependents(
    client: AsyncClient,
    db_session: AsyncSession,
    make_trainer: MakeTrainer,
    make_user: MakeUser,
    make_player: MakePlayer,
    make_dependent: MakeDependent,
    add_share_link: AddShareLink,
    principal_token: PrincipalToken,
) -> None:
    parent = await make_user()
    own = await make_player(owner=parent)
    kid = await make_dependent(parent=parent)
    link = await add_share_link(await make_trainer())
    r = await client.post(url(link.code), json=body(), headers=bearer(principal_token(parent)))
    assert r.status_code == 200
    assert len(await _links_of(db_session, own.id)) == 1
    assert await _links_of(db_session, kid.id) == []


async def test_profile_ids_not_owned_are_404_not_found(
    client: AsyncClient,
    db_session: AsyncSession,
    make_trainer: MakeTrainer,
    make_user: MakeUser,
    make_player: MakePlayer,
    make_dependent: MakeDependent,
    add_share_link: AddShareLink,
    principal_token: PrincipalToken,
) -> None:
    parent = await make_user()
    mine = await make_dependent(parent=parent)
    foreign = await make_dependent()
    link = await add_share_link(await make_trainer())
    headers = bearer(principal_token(parent))
    for ids in ([str(foreign.id)], [str(mine.id), str(foreign.id)], [str(uuid4())]):
        r = await client.post(url(link.code), json=body(player_profile_ids=ids), headers=headers)
        assert r.status_code == 404
        assert code(r) == "NOT_FOUND"
    assert await _links_of(db_session, mine.id) == []  # all-or-nothing
    await db_session.refresh(link)
    assert link.use_count == 0


async def test_new_account_cannot_name_existing_profiles(
    client: AsyncClient,
    make_trainer: MakeTrainer,
    make_dependent: MakeDependent,
    add_share_link: AddShareLink,
) -> None:
    kid = await make_dependent()
    link = await add_share_link(await make_trainer())
    r = await client.post(url(link.code), json=body(player_profile_ids=[str(kid.id)]))
    assert r.status_code == 404
    assert code(r) == "NOT_FOUND"


async def test_child_cannot_redeem_403_and_notifies_parent(
    client: AsyncClient,
    db_session: AsyncSession,
    make_trainer: MakeTrainer,
    make_dependent: MakeDependent,
    add_share_link: AddShareLink,
    principal_token: PrincipalToken,
    jobs: FakeJobQueue,
) -> None:
    profile = await make_dependent(with_login=True)
    assert profile.login_user_id is not None
    child = await db_session.get(User, profile.login_user_id)
    assert child is not None
    link = await add_share_link(await make_trainer())
    token = principal_token(child, child=True)

    r = await client.post(url(link.code), json=body(), headers=bearer(token))
    assert r.status_code == 403
    assert code(r) == "CHILD_CANNOT_REDEEM"
    assert [(j.name, j.kwargs) for j in jobs.enqueued] == [
        (
            "notify_parent_share_link_blocked",
            {"player_profile_id": str(profile.id), "share_link_id": str(link.id)},
        )
    ]
    await db_session.refresh(link)
    assert link.use_count == 0


async def test_unverified_user_cannot_redeem_403(
    client: AsyncClient,
    db_session: AsyncSession,
    make_trainer: MakeTrainer,
    make_player: MakePlayer,
    make_user: MakeUser,
    add_share_link: AddShareLink,
    principal_token: PrincipalToken,
    jobs: FakeJobQueue,
) -> None:
    owner = await make_user()
    owner.is_email_verified = False
    await make_player(owner=owner)
    link = await add_share_link(await make_trainer())
    r = await client.post(url(link.code), json=body(), headers=bearer(principal_token(owner)))
    assert r.status_code == 403
    assert code(r) == "EMAIL_NOT_VERIFIED"
    assert jobs.enqueued == []


async def test_wrong_link_kind_for_authenticated_role(
    client: AsyncClient,
    make_trainer: MakeTrainer,
    make_user: MakeUser,
    add_share_link: AddShareLink,
    principal_token: PrincipalToken,
) -> None:
    trainer = await make_trainer()
    static = await add_share_link(trainer)
    coach_link = await add_share_link(trainer, kind="coach_unique", target_email="p@x.com")
    coach = await make_user(role=UserRole.coach)
    player = await make_user(email="p@x.com")

    r = await client.post(url(static.code), json=body(), headers=bearer(principal_token(coach)))
    assert (r.status_code, code(r)) == (409, "WRONG_LINK_KIND")
    r = await client.post(
        url(coach_link.code), json=body(), headers=bearer(principal_token(player))
    )
    assert (r.status_code, code(r)) == (409, "WRONG_LINK_KIND")


async def test_authenticated_invalid_link_is_404_before_role_checks(
    client: AsyncClient, make_user: MakeUser, principal_token: PrincipalToken
) -> None:
    user = await make_user()
    r = await client.post(url("nope"), json=body(), headers=bearer(principal_token(user)))
    assert (r.status_code, code(r)) == (404, "SHARE_LINK_INVALID")


# --- rate limit ----------------------------------------------------------------------------


async def test_register_rate_limit_20_per_hour_per_ip(client: AsyncClient) -> None:
    for _ in range(20):
        assert (await client.post(url("nope"), json=body())).status_code == 404
    r = await client.post(url("nope"), json=body())
    assert r.status_code == 429
    assert code(r) == "RATE_LIMITED"
    assert int(r.headers["Retry-After"]) >= 1


async def test_login_works_after_registration(
    client: AsyncClient, make_trainer: MakeTrainer, add_share_link: AddShareLink
) -> None:
    link = await add_share_link(await make_trainer())
    await client.post(url(link.code), json=body())
    assert (await login(client, "new@x.com", STRONG))["user"]["role"] == "player"
