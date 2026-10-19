from typing import Any
from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import new_id
from app.models.identity import (
    LinkStatus,
    PlayerProfile,
    RefreshToken,
    TrainerPlayerLink,
    User,
    UserRole,
    UserStatus,
)
from tests.api.auth.helpers import login
from tests.api.helpers import bearer
from tests.factories import (
    LinkPlayer,
    MakeDependent,
    MakePlayer,
    MakeTrainer,
    MakeUser,
    PrincipalToken,
)
from tests.fakes import FakeClock, FakeCommitmentChecker

PLAYERS = "/api/v1/me/players"
CONTEXTS = "/api/v1/me/contexts"
STRONG = "Tr1cky-Purple-Elephant-42!"


def code(response: Any) -> str:
    return str(response.json()["error"]["code"])


def dependent_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "display_name": "Mia",
        "birth_date": "2015-03-01",
        "gender": "female",
    }
    return {**body, **overrides}


async def parent_with_trainer(
    make_user: MakeUser, make_player: MakePlayer, make_trainer: MakeTrainer, link_player: LinkPlayer
) -> tuple[User, PlayerProfile, User]:
    """A parent with an own profile linked to one trainer (so the trainer is "theirs")."""
    parent = await make_user(email="parent@x.com")
    own = await make_player(owner=parent, display_name="Pat Parent")
    trainer = await make_trainer()
    await link_player(trainer, own)
    return parent, own, trainer


# --- create ----------------------------------------------------------------------------------


async def test_create_dependent_links_trainers_as_parent_added(
    client: AsyncClient,
    make_user: MakeUser,
    make_player: MakePlayer,
    make_trainer: MakeTrainer,
    link_player: LinkPlayer,
    principal_token: PrincipalToken,
    db_session: AsyncSession,
) -> None:
    parent, _, trainer = await parent_with_trainer(
        make_user, make_player, make_trainer, link_player
    )

    r = await client.post(
        PLAYERS,
        json=dependent_body(skill_level="beginner", trainer_ids=[str(trainer.id)] * 2),
        headers=bearer(principal_token(parent)),
    )

    assert r.status_code == 201, r.text
    body = r.json()
    assert body["display_name"] == "Mia"
    assert body["is_dependent"] is True
    assert body["has_login"] is False
    assert body["token_spend_requires_approval"] is False
    assert body["skill_level"] == "beginner"
    assert [(t["trainer_id"], t["status"], t["source"]) for t in body["trainers"]] == [
        (str(trainer.id), "active", "parent_added")
    ]
    assert body["trainers"][0]["business_name"].startswith("Academy")
    row = await db_session.get(PlayerProfile, body["id"])
    assert row is not None
    assert (row.owner_user_id, row.login_user_id, row.is_dependent) == (parent.id, None, True)


async def test_create_dependent_with_child_login_creates_active_preverified_user(
    client: AsyncClient,
    make_user: MakeUser,
    principal_token: PrincipalToken,
    db_session: AsyncSession,
) -> None:
    parent = await make_user()
    r = await client.post(
        PLAYERS,
        json=dependent_body(login={"email": "Mia@x.com", "password": STRONG}),
        headers=bearer(principal_token(parent)),
    )

    assert r.status_code == 201, r.text
    assert r.json()["has_login"] is True
    assert "password" not in r.text
    profile = await db_session.get(PlayerProfile, r.json()["id"])
    assert profile is not None
    assert profile.login_user_id is not None
    child = await db_session.get(User, profile.login_user_id)
    assert child is not None
    assert (child.role, child.status, child.is_email_verified) == (
        UserRole.player,
        UserStatus.active,
        True,
    )
    assert child.email.lower() == "mia@x.com"
    session = await login(client, "mia@x.com", STRONG)
    assert session["user"]["child"] is True


async def test_create_dependent_age_limits_422_invalid_age(
    client: AsyncClient, make_user: MakeUser, principal_token: PrincipalToken, clock: FakeClock
) -> None:
    headers = bearer(principal_token(await make_user()))
    for birth in ("2008-10-18", "2025-10-19", "2030-01-01"):
        r = await client.post(PLAYERS, json=dependent_body(birth_date=birth), headers=headers)
        assert (r.status_code, code(r)) == (422, "INVALID_AGE"), birth
    ok = await client.post(PLAYERS, json=dependent_body(birth_date="2008-10-19"), headers=headers)
    assert ok.status_code == 201


async def test_create_dependent_trainer_not_linked_404(
    client: AsyncClient,
    make_user: MakeUser,
    make_player: MakePlayer,
    make_trainer: MakeTrainer,
    link_player: LinkPlayer,
    principal_token: PrincipalToken,
    db_session: AsyncSession,
) -> None:
    parent, _, _ = await parent_with_trainer(make_user, make_player, make_trainer, link_player)
    stranger_trainer = await make_trainer()
    inactive_trainer = await make_trainer(status=UserStatus.inactive)
    headers = bearer(principal_token(parent))

    for trainer_id in (stranger_trainer.id, inactive_trainer.id, uuid4()):
        r = await client.post(
            PLAYERS, json=dependent_body(trainer_ids=[str(trainer_id)]), headers=headers
        )
        assert (r.status_code, code(r)) == (404, "NOT_FOUND")
    created = (
        await db_session.execute(select(PlayerProfile).where(PlayerProfile.is_dependent))
    ).scalars()
    assert list(created) == []  # nothing half-created


async def test_duplicate_player_409_unless_allow_duplicate(
    client: AsyncClient, make_user: MakeUser, principal_token: PrincipalToken
) -> None:
    headers = bearer(principal_token(await make_user()))
    assert (await client.post(PLAYERS, json=dependent_body(), headers=headers)).status_code == 201

    dup = await client.post(PLAYERS, json=dependent_body(display_name="MIA"), headers=headers)
    assert (dup.status_code, code(dup)) == (409, "DUPLICATE_PLAYER")

    forced = await client.post(
        f"{PLAYERS}?allow_duplicate=true", json=dependent_body(), headers=headers
    )
    assert forced.status_code == 201
    other_day = await client.post(
        PLAYERS, json=dependent_body(birth_date="2015-03-02"), headers=headers
    )
    assert other_day.status_code == 201


async def test_child_login_email_taken_409_and_weak_password_422(
    client: AsyncClient,
    make_user: MakeUser,
    principal_token: PrincipalToken,
    db_session: AsyncSession,
) -> None:
    await make_user(email="taken@x.com")
    headers = bearer(principal_token(await make_user()))

    taken = await client.post(
        PLAYERS,
        json=dependent_body(login={"email": "taken@x.com", "password": STRONG}),
        headers=headers,
    )
    assert (taken.status_code, code(taken)) == (409, "EMAIL_TAKEN")
    weak = await client.post(
        PLAYERS,
        json=dependent_body(login={"email": "new@x.com", "password": "aaaaaaaaaaaa"}),
        headers=headers,
    )
    assert (weak.status_code, code(weak)) == (422, "WEAK_PASSWORD")
    short = await client.post(
        PLAYERS,
        json=dependent_body(login={"email": "new@x.com", "password": "short"}),
        headers=headers,
    )
    assert (short.status_code, code(short)) == (422, "VALIDATION_ERROR")
    assert (await db_session.execute(select(PlayerProfile))).first() is None


async def test_create_validation_422(
    client: AsyncClient, make_user: MakeUser, principal_token: PrincipalToken
) -> None:
    headers = bearer(principal_token(await make_user()))
    for body in (
        dependent_body(display_name=""),
        dependent_body(gender="robot"),
        dependent_body(trainer_ids=[str(uuid4())] * 21),
        dependent_body(role="super_admin"),
        {"display_name": "x", "birth_date": "2015-03-01"},
    ):
        r = await client.post(PLAYERS, json=body, headers=headers)
        assert (r.status_code, code(r)) == (422, "VALIDATION_ERROR"), body


async def test_child_cannot_create_dependent_403(
    client: AsyncClient,
    make_user: MakeUser,
    make_dependent: MakeDependent,
    principal_token: PrincipalToken,
    db_session: AsyncSession,
) -> None:
    child_profile = await make_dependent(parent=await make_user(), with_login=True)
    child = await db_session.get(User, child_profile.login_user_id)
    assert child is not None
    r = await client.post(
        PLAYERS, json=dependent_body(), headers=bearer(principal_token(child, child=True))
    )
    assert (r.status_code, code(r)) == (403, "CHILD_CANNOT_CREATE_DEPENDENT")


async def test_staff_and_anonymous_cannot_use_players(
    client: AsyncClient,
    make_user: MakeUser,
    make_trainer: MakeTrainer,
    principal_token: PrincipalToken,
) -> None:
    for user in (await make_trainer(), await make_user(role=UserRole.super_admin)):
        headers = bearer(principal_token(user))
        assert (await client.get(PLAYERS, headers=headers)).status_code == 403
        assert (
            await client.post(PLAYERS, json=dependent_body(), headers=headers)
        ).status_code == 403
    assert (await client.get(PLAYERS)).status_code == 401
    assert (await client.get(CONTEXTS)).status_code == 401


# --- read ------------------------------------------------------------------------------------


async def test_list_players_own_first_then_dependents(
    client: AsyncClient,
    make_user: MakeUser,
    make_player: MakePlayer,
    make_dependent: MakeDependent,
    principal_token: PrincipalToken,
) -> None:
    parent = await make_user()
    first_child = await make_dependent(parent=parent)
    own = await make_player(owner=parent)  # created after the dependent, still listed first
    second_child = await make_dependent(parent=parent)
    await make_dependent(parent=await make_user())  # someone else's

    r = await client.get(PLAYERS, headers=bearer(principal_token(parent)))

    assert r.status_code == 200
    assert [p["id"] for p in r.json()] == [str(own.id), str(first_child.id), str(second_child.id)]
    assert [p["is_dependent"] for p in r.json()] == [False, True, True]


async def test_get_player_not_owner_404_child_reads_only_self(
    client: AsyncClient,
    make_user: MakeUser,
    make_dependent: MakeDependent,
    principal_token: PrincipalToken,
    db_session: AsyncSession,
) -> None:
    parent = await make_user()
    child_profile = await make_dependent(parent=parent, with_login=True)
    sibling = await make_dependent(parent=parent)
    child = await db_session.get(User, child_profile.login_user_id)
    assert child is not None

    stranger = await client.get(
        f"{PLAYERS}/{child_profile.id}", headers=bearer(principal_token(await make_user()))
    )
    assert (stranger.status_code, code(stranger)) == (404, "NOT_FOUND")
    owner = await client.get(
        f"{PLAYERS}/{child_profile.id}", headers=bearer(principal_token(parent))
    )
    assert owner.status_code == 200
    assert owner.json()["has_login"] is True

    headers = bearer(principal_token(child, child=True))
    own = await client.get(f"{PLAYERS}/{child_profile.id}", headers=headers)
    assert own.status_code == 200
    assert (await client.get(f"{PLAYERS}/{sibling.id}", headers=headers)).status_code == 404
    listed = await client.get(PLAYERS, headers=headers)
    assert [p["id"] for p in listed.json()] == [str(child_profile.id)]
    assert (await client.get(f"{PLAYERS}/{uuid4()}", headers=headers)).status_code == 404


# --- update ----------------------------------------------------------------------------------


async def test_patch_updates_profile_fields_only_those_sent(
    client: AsyncClient,
    make_user: MakeUser,
    make_dependent: MakeDependent,
    principal_token: PrincipalToken,
) -> None:
    parent = await make_user()
    child = await make_dependent(parent=parent)
    headers = bearer(principal_token(parent))

    r = await client.patch(
        f"{PLAYERS}/{child.id}",
        json={"display_name": "Zoe", "skill_level": "advanced", "gender": "other"},
        headers=headers,
    )

    assert r.status_code == 200, r.text
    assert (r.json()["display_name"], r.json()["skill_level"], r.json()["gender"]) == (
        "Zoe",
        "advanced",
        "other",
    )
    assert r.json()["birth_date"] == child.birth_date.isoformat()
    cleared = await client.patch(
        f"{PLAYERS}/{child.id}", json={"skill_level": None}, headers=headers
    )
    assert cleared.json()["skill_level"] is None
    assert cleared.json()["display_name"] == "Zoe"


async def test_patch_validation_and_age_rule(
    client: AsyncClient,
    make_user: MakeUser,
    make_player: MakePlayer,
    make_dependent: MakeDependent,
    principal_token: PrincipalToken,
) -> None:
    parent = await make_user()
    own = await make_player(owner=parent)
    child = await make_dependent(parent=parent)
    headers = bearer(principal_token(parent))

    for body in ({"display_name": None}, {"birth_date": None}, {"unknown": 1}, {"gender": "x"}):
        r = await client.patch(f"{PLAYERS}/{child.id}", json=body, headers=headers)
        assert (r.status_code, code(r)) == (422, "VALIDATION_ERROR"), body
    too_old = await client.patch(
        f"{PLAYERS}/{child.id}", json={"birth_date": "2000-01-01"}, headers=headers
    )
    assert (too_old.status_code, code(too_old)) == (422, "INVALID_AGE")
    adult_own = await client.patch(
        f"{PLAYERS}/{own.id}", json={"birth_date": "1990-01-01"}, headers=headers
    )
    assert adult_own.status_code == 200  # the age rule is for dependents only
    own_login = await client.patch(
        f"{PLAYERS}/{own.id}",
        json={"login": {"email": "me@x.com", "password": STRONG}},
        headers=headers,
    )
    assert (own_login.status_code, code(own_login)) == (422, "VALIDATION_ERROR")


async def test_patch_not_owner_404_child_403(
    client: AsyncClient,
    make_user: MakeUser,
    make_dependent: MakeDependent,
    principal_token: PrincipalToken,
    db_session: AsyncSession,
) -> None:
    parent = await make_user()
    child_profile = await make_dependent(parent=parent, with_login=True)
    child = await db_session.get(User, child_profile.login_user_id)
    assert child is not None

    other = await client.patch(
        f"{PLAYERS}/{child_profile.id}",
        json={"display_name": "x"},
        headers=bearer(principal_token(await make_user())),
    )
    assert other.status_code == 404
    as_child = await client.patch(
        f"{PLAYERS}/{child_profile.id}",
        json={"display_name": "x"},
        headers=bearer(principal_token(child, child=True)),
    )
    assert as_child.status_code == 403


async def test_patch_login_absent_unchanged_null_revokes_object_replaces(
    client: AsyncClient,
    make_user: MakeUser,
    make_dependent: MakeDependent,
    principal_token: PrincipalToken,
    db_session: AsyncSession,
    clock: FakeClock,
) -> None:
    parent = await make_user()
    child = await make_dependent(parent=parent)
    headers = bearer(principal_token(parent))
    url = f"{PLAYERS}/{child.id}"

    # object on a dependent without a login: create
    created = await client.patch(
        url, json={"login": {"email": "kid@x.com", "password": STRONG}}, headers=headers
    )
    assert created.status_code == 200, created.text
    assert created.json()["has_login"] is True
    await db_session.refresh(child)
    login_user_id = child.login_user_id
    assert login_user_id is not None
    kid_session = await login(client, "kid@x.com", STRONG)

    # absent: unchanged
    same = await client.patch(url, json={"display_name": "Renamed"}, headers=headers)
    assert same.json()["has_login"] is True
    kid = await db_session.get(User, login_user_id)
    assert kid is not None
    assert kid.status is UserStatus.active

    # null: revoke (inactive, families revoked, login_user_id retained)
    revoked = await client.patch(url, json={"login": None}, headers=headers)
    assert revoked.status_code == 200
    assert revoked.json()["has_login"] is False
    await db_session.refresh(kid)
    await db_session.refresh(child)
    assert [kid.status] == [UserStatus.inactive]  # list: mypy narrowed the earlier assert
    assert child.login_user_id == login_user_id
    live = (
        await db_session.execute(
            select(RefreshToken).where(
                RefreshToken.user_id == login_user_id, RefreshToken.revoked_at.is_(None)
            )
        )
    ).all()
    assert live == []
    assert (
        await client.get("/api/v1/me", headers=bearer(kid_session["tokens"]["access_token"]))
    ).status_code == 401

    # object again: replaces the credentials and reactivates the same user
    replaced = await client.patch(
        url, json={"login": {"email": "kid2@x.com", "password": STRONG + "x"}}, headers=headers
    )
    assert replaced.status_code == 200
    assert replaced.json()["has_login"] is True
    await db_session.refresh(kid)
    await db_session.refresh(child)
    assert (kid.email, kid.status, child.login_user_id) == (
        "kid2@x.com",
        UserStatus.active,
        login_user_id,
    )
    await login(client, "kid2@x.com", STRONG + "x")

    # null on a dependent that never had a login is a no-op
    other = await make_dependent(parent=parent)
    noop = await client.patch(f"{PLAYERS}/{other.id}", json={"login": None}, headers=headers)
    assert (noop.status_code, noop.json()["has_login"]) == (200, False)


async def test_patch_login_email_taken_409_and_weak_422(
    client: AsyncClient,
    make_user: MakeUser,
    make_dependent: MakeDependent,
    principal_token: PrincipalToken,
) -> None:
    await make_user(email="taken@x.com")
    parent = await make_user()
    child = await make_dependent(parent=parent)
    headers = bearer(principal_token(parent))

    taken = await client.patch(
        f"{PLAYERS}/{child.id}",
        json={"login": {"email": "taken@x.com", "password": STRONG}},
        headers=headers,
    )
    assert (taken.status_code, code(taken)) == (409, "EMAIL_TAKEN")
    weak = await client.patch(
        f"{PLAYERS}/{child.id}",
        json={"login": {"email": "new@x.com", "password": "aaaaaaaaaaaa"}},
        headers=headers,
    )
    assert (weak.status_code, code(weak)) == (422, "WEAK_PASSWORD")


# --- remove ----------------------------------------------------------------------------------


async def test_delete_dependent_soft_removes_and_keeps_history(
    client: AsyncClient,
    make_user: MakeUser,
    make_player: MakePlayer,
    make_trainer: MakeTrainer,
    make_dependent: MakeDependent,
    link_player: LinkPlayer,
    principal_token: PrincipalToken,
    db_session: AsyncSession,
    commitments: FakeCommitmentChecker,
) -> None:
    parent, _, trainer = await parent_with_trainer(
        make_user, make_player, make_trainer, link_player
    )
    child = await make_dependent(parent=parent, with_login=True)
    await link_player(trainer, child)
    login_user_id = child.login_user_id
    headers = bearer(principal_token(parent))

    r = await client.delete(f"{PLAYERS}/{child.id}", headers=headers)

    assert r.status_code == 204
    assert commitments.calls == [(child.id, trainer.id)]
    await db_session.refresh(child)
    assert child.is_active is False  # the row, and its history, stay
    link = (
        await db_session.execute(
            select(TrainerPlayerLink).where(TrainerPlayerLink.player_profile_id == child.id)
        )
    ).scalar_one()
    assert link.status is LinkStatus.inactive
    login_user = await db_session.get(User, login_user_id)
    assert login_user is not None
    assert login_user.status is UserStatus.inactive
    assert child.login_user_id == login_user_id
    assert (await client.get(f"{PLAYERS}/{child.id}", headers=headers)).status_code == 404
    listed = await client.get(PLAYERS, headers=headers)
    assert [p["id"] for p in listed.json()] != [] and str(child.id) not in [
        p["id"] for p in listed.json()
    ]
    assert (await client.delete(f"{PLAYERS}/{child.id}", headers=headers)).status_code == 404
    # a removed dependent can be created again
    again = await client.post(
        PLAYERS,
        json=dependent_body(
            display_name=child.display_name, birth_date=child.birth_date.isoformat()
        ),
        headers=headers,
    )
    assert again.status_code == 201


async def test_delete_dependent_with_commitments_409_lists_rsvp_ids(
    client: AsyncClient,
    make_user: MakeUser,
    make_player: MakePlayer,
    make_trainer: MakeTrainer,
    make_dependent: MakeDependent,
    link_player: LinkPlayer,
    principal_token: PrincipalToken,
    db_session: AsyncSession,
    commitments: FakeCommitmentChecker,
) -> None:
    parent, _, trainer = await parent_with_trainer(
        make_user, make_player, make_trainer, link_player
    )
    child = await make_dependent(parent=parent, with_login=True)
    await link_player(trainer, child)
    rsvps = [new_id(), new_id()]
    commitments.rsvps = rsvps

    r = await client.delete(f"{PLAYERS}/{child.id}", headers=bearer(principal_token(parent)))

    assert (r.status_code, code(r)) == (409, "ACTIVE_COMMITMENTS")
    assert r.json()["error"]["details"] == [{"rsvp_ids": [str(i) for i in rsvps]}]
    await db_session.refresh(child)
    assert child.is_active is True
    login_user = await db_session.get(User, child.login_user_id)
    assert login_user is not None
    assert login_user.status is UserStatus.active


async def test_delete_own_profile_409_cannot_remove_own_profile(
    client: AsyncClient,
    make_user: MakeUser,
    make_player: MakePlayer,
    principal_token: PrincipalToken,
) -> None:
    parent = await make_user()
    own = await make_player(owner=parent)
    r = await client.delete(f"{PLAYERS}/{own.id}", headers=bearer(principal_token(parent)))
    assert (r.status_code, code(r)) == (409, "CANNOT_REMOVE_OWN_PROFILE")


async def test_delete_not_owner_404_child_403(
    client: AsyncClient,
    make_user: MakeUser,
    make_dependent: MakeDependent,
    principal_token: PrincipalToken,
    db_session: AsyncSession,
) -> None:
    parent = await make_user()
    child_profile = await make_dependent(parent=parent, with_login=True)
    child = await db_session.get(User, child_profile.login_user_id)
    assert child is not None
    other = await client.delete(
        f"{PLAYERS}/{child_profile.id}", headers=bearer(principal_token(await make_user()))
    )
    assert other.status_code == 404
    as_child = await client.delete(
        f"{PLAYERS}/{child_profile.id}", headers=bearer(principal_token(child, child=True))
    )
    assert as_child.status_code == 403


# --- trainer links ---------------------------------------------------------------------------


async def test_add_trainer_link_201_then_200_idempotent_and_reactivates(
    client: AsyncClient,
    make_user: MakeUser,
    make_player: MakePlayer,
    make_trainer: MakeTrainer,
    make_dependent: MakeDependent,
    link_player: LinkPlayer,
    principal_token: PrincipalToken,
    db_session: AsyncSession,
) -> None:
    parent, _, trainer = await parent_with_trainer(
        make_user, make_player, make_trainer, link_player
    )
    child = await make_dependent(parent=parent)
    headers = bearer(principal_token(parent))
    url = f"{PLAYERS}/{child.id}/trainers"
    body = {"trainer_id": str(trainer.id)}

    first = await client.post(url, json=body, headers=headers)
    assert first.status_code == 201, first.text
    assert (first.json()["trainer_id"], first.json()["status"], first.json()["source"]) == (
        str(trainer.id),
        "active",
        "parent_added",
    )
    second = await client.post(url, json=body, headers=headers)
    assert (second.status_code, second.json()) == (200, first.json())

    assert (await client.delete(f"{url}/{trainer.id}", headers=headers)).status_code == 204
    reactivated = await client.post(url, json=body, headers=headers)
    assert (reactivated.status_code, reactivated.json()["status"]) == (201, "active")
    links = (
        await db_session.execute(
            select(TrainerPlayerLink).where(TrainerPlayerLink.player_profile_id == child.id)
        )
    ).scalars()
    assert len(list(links)) == 1


async def test_add_trainer_link_404_cases_and_403_child(
    client: AsyncClient,
    make_user: MakeUser,
    make_player: MakePlayer,
    make_trainer: MakeTrainer,
    make_dependent: MakeDependent,
    link_player: LinkPlayer,
    principal_token: PrincipalToken,
    db_session: AsyncSession,
) -> None:
    parent, _, trainer = await parent_with_trainer(
        make_user, make_player, make_trainer, link_player
    )
    child_profile = await make_dependent(parent=parent, with_login=True)
    headers = bearer(principal_token(parent))
    url = f"{PLAYERS}/{child_profile.id}/trainers"
    unrelated = await make_trainer()

    for trainer_id in (unrelated.id, uuid4()):
        r = await client.post(url, json={"trainer_id": str(trainer_id)}, headers=headers)
        assert r.status_code == 404
    not_owner = await client.post(
        url,
        json={"trainer_id": str(trainer.id)},
        headers=bearer(principal_token(await make_user())),
    )
    assert not_owner.status_code == 404
    child = await db_session.get(User, child_profile.login_user_id)
    assert child is not None
    as_child = await client.post(
        url,
        json={"trainer_id": str(trainer.id)},
        headers=bearer(principal_token(child, child=True)),
    )
    assert as_child.status_code == 403
    extra = await client.post(url, json={"trainer_id": str(trainer.id), "x": 1}, headers=headers)
    assert extra.status_code == 422


async def test_remove_trainer_link_soft_and_commitments_409(
    client: AsyncClient,
    make_user: MakeUser,
    make_player: MakePlayer,
    make_trainer: MakeTrainer,
    make_dependent: MakeDependent,
    link_player: LinkPlayer,
    principal_token: PrincipalToken,
    db_session: AsyncSession,
    commitments: FakeCommitmentChecker,
) -> None:
    parent, _, trainer = await parent_with_trainer(
        make_user, make_player, make_trainer, link_player
    )
    child = await make_dependent(parent=parent)
    link = await link_player(trainer, child)
    headers = bearer(principal_token(parent))
    url = f"{PLAYERS}/{child.id}/trainers/{trainer.id}"
    rsvp = new_id()
    commitments.rsvps = [rsvp]

    blocked = await client.delete(url, headers=headers)
    assert (blocked.status_code, code(blocked)) == (409, "ACTIVE_COMMITMENTS")
    assert blocked.json()["error"]["details"] == [{"rsvp_ids": [str(rsvp)]}]
    await db_session.refresh(link)
    assert link.status is LinkStatus.active

    commitments.rsvps = []
    assert (await client.delete(url, headers=headers)).status_code == 204
    await db_session.refresh(link)
    assert [link.status] == [LinkStatus.inactive]  # soft: the row stays
    assert (await client.delete(url, headers=headers)).status_code == 404  # no active link left
    shown = await client.get(f"{PLAYERS}/{child.id}", headers=headers)
    assert [(t["trainer_id"], t["status"]) for t in shown.json()["trainers"]] == [
        (str(trainer.id), "inactive")
    ]
    other = await client.delete(url, headers=bearer(principal_token(await make_user())))
    assert other.status_code == 404


# --- contexts --------------------------------------------------------------------------------


async def test_contexts_lists_active_links_omits_deactivated_trainers(
    client: AsyncClient,
    make_user: MakeUser,
    make_player: MakePlayer,
    make_trainer: MakeTrainer,
    make_dependent: MakeDependent,
    link_player: LinkPlayer,
    principal_token: PrincipalToken,
    db_session: AsyncSession,
) -> None:
    parent = await make_user()
    own = await make_player(owner=parent)
    child = await make_dependent(parent=parent)
    removed = await make_dependent(parent=parent)
    t1, t2, t_inactive, t_unlinked = (
        await make_trainer(),
        await make_trainer(),
        await make_trainer(),
        await make_trainer(),
    )
    await link_player(t1, own)
    await link_player(t1, child)
    await link_player(t2, child)
    await link_player(t_inactive, own)
    inactive_link = await link_player(t_unlinked, own)
    inactive_link.status = LinkStatus.inactive
    await link_player(t2, removed)
    t_inactive.status = UserStatus.inactive
    await db_session.flush()
    headers = bearer(principal_token(parent))
    assert (await client.delete(f"{PLAYERS}/{removed.id}", headers=headers)).status_code == 204

    r = await client.get(CONTEXTS, headers=headers)

    assert r.status_code == 200, r.text
    by_trainer = {c["trainer_id"]: c for c in r.json()}
    assert set(by_trainer) == {str(t1.id), str(t2.id)}
    assert sorted(by_trainer[str(t1.id)]["player_profile_ids"]) == sorted(
        [str(own.id), str(child.id)]
    )
    assert by_trainer[str(t2.id)]["player_profile_ids"] == [str(child.id)]
    assert by_trainer[str(t1.id)]["logo_url"] is None
    assert by_trainer[str(t1.id)]["business_name"].startswith("Academy")
    assert "linked_at" in by_trainer[str(t1.id)]


async def test_contexts_non_player_and_child_403(
    client: AsyncClient,
    make_user: MakeUser,
    make_trainer: MakeTrainer,
    make_dependent: MakeDependent,
    principal_token: PrincipalToken,
    db_session: AsyncSession,
) -> None:
    child_profile = await make_dependent(parent=await make_user(), with_login=True)
    child = await db_session.get(User, child_profile.login_user_id)
    assert child is not None
    for principal in (
        principal_token(await make_trainer()),
        principal_token(await make_user(role=UserRole.super_admin)),
        principal_token(child, child=True),
    ):
        r = await client.get(CONTEXTS, headers=bearer(principal))
        assert (r.status_code, code(r)) == (403, "FORBIDDEN")


async def test_contexts_empty_for_player_without_links(
    client: AsyncClient, make_user: MakeUser, principal_token: PrincipalToken
) -> None:
    r = await client.get(CONTEXTS, headers=bearer(principal_token(await make_user())))
    assert (r.status_code, r.json()) == (200, [])


async def test_patch_replacing_existing_login_with_taken_email_409_keeps_old_login(
    client: AsyncClient,
    make_user: MakeUser,
    make_dependent: MakeDependent,
    principal_token: PrincipalToken,
    db_session: AsyncSession,
) -> None:
    await make_user(email="taken@x.com")
    parent = await make_user()
    child = await make_dependent(parent=parent, with_login=True)
    kid = await db_session.get(User, child.login_user_id)
    assert kid is not None
    old_email = kid.email

    r = await client.patch(
        f"{PLAYERS}/{child.id}",
        json={"login": {"email": "taken@x.com", "password": STRONG}},
        headers=bearer(principal_token(parent)),
    )

    assert (r.status_code, code(r)) == (409, "EMAIL_TAKEN")
    await db_session.refresh(kid)
    assert kid.email == old_email


async def test_create_dependent_one_unlinked_trainer_among_valid_ones_404(
    client: AsyncClient,
    make_user: MakeUser,
    make_player: MakePlayer,
    make_trainer: MakeTrainer,
    link_player: LinkPlayer,
    principal_token: PrincipalToken,
) -> None:
    parent, trainer, _ = await parent_with_trainer(
        make_user, make_player, make_trainer, link_player
    )
    stranger = await make_trainer()

    r = await client.post(
        PLAYERS,
        json=dependent_body(trainer_ids=[str(trainer.id), str(stranger.id)]),
        headers=bearer(principal_token(parent)),
    )

    assert (r.status_code, code(r)) == (404, "NOT_FOUND")
