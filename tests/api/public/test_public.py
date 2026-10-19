from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import salted_hash
from app.models.identity import ShareLinkEvent, TrainerProfile, UserStatus
from tests.factories import AddShareLink, MakeUser
from tests.fakes import FakeClock

PREVIEW = "/api/v1/public/share-links"
BRANDING = "/api/v1/public/trainers"


def code(response: Any) -> str:
    return str(response.json()["error"]["code"])


async def named_trainer(db_session: AsyncSession, make_trainer: MakeUser) -> Any:
    trainer = await make_trainer(first_name="Ana", last_name="Lopez")
    profile = await db_session.get(TrainerProfile, trainer.id)
    assert profile is not None
    profile.business_name = "Acme Padel"
    profile.slug = "acme-padel"
    profile.logo_key = "logos/abc"
    profile.brand_primary_color = "#112233"
    profile.brand_secondary_color = "#AABBCC"
    return trainer


async def test_preview_valid_static_and_coach_unique_with_email_hint(
    client: AsyncClient,
    make_trainer: MakeUser,
    add_share_link: AddShareLink,
    db_session: AsyncSession,
) -> None:
    trainer = await named_trainer(db_session, make_trainer)
    static = await add_share_link(trainer)
    unique = await add_share_link(trainer, kind="coach_unique", target_email="sam@example.com")

    r = await client.get(f"{PREVIEW}/{static.code}")
    assert r.status_code == 200
    assert r.json() == {
        "trainer_name": "Ana Lopez",
        "business_name": "Acme Padel",
        "logo_url": "https://media.example.com/logos/abc",
        "kind": "player_static",
        "valid": True,
        "target_email_hint": None,
    }

    r = await client.get(f"{PREVIEW}/{unique.code}")
    assert r.status_code == 200
    assert r.json()["kind"] == "coach_unique"
    assert r.json()["target_email_hint"] == "s***@example.com"
    assert "sam@example.com" not in r.text


async def test_preview_records_opened_event_with_hashed_ip_and_ua(
    client: AsyncClient,
    make_trainer: MakeUser,
    add_share_link: AddShareLink,
    db_session: AsyncSession,
) -> None:
    link = await add_share_link(await make_trainer())
    await client.get(f"{PREVIEW}/{link.code}", headers={"User-Agent": "pytest-agent"})

    events = list(
        (
            await db_session.execute(
                select(ShareLinkEvent).where(ShareLinkEvent.share_link_id == link.id)
            )
        ).scalars()
    )
    assert [e.kind for e in events] == ["opened"]
    salt = "s" * 16
    assert events[0].user_agent_hash == salted_hash(salt, "pytest-agent")
    assert events[0].ip_hash == salted_hash(salt, "127.0.0.1")
    assert events[0].ip_hash != "127.0.0.1"


@pytest.mark.parametrize(
    "state", ["unknown", "inactive", "expired", "exhausted", "trainer_deactivated"]
)
async def test_preview_invalid_all_404_share_link_invalid(
    client: AsyncClient,
    make_trainer: MakeUser,
    add_share_link: AddShareLink,
    db_session: AsyncSession,
    clock: FakeClock,
    state: str,
) -> None:
    trainer = await make_trainer()
    link = await add_share_link(trainer, kind="coach_unique", target_email="c@example.com")
    code_ = "does-not-exist"
    if state != "unknown":
        code_ = link.code
    if state == "inactive":
        link.is_active = False
    elif state == "expired":
        clock.advance(days=7, seconds=1)
    elif state == "exhausted":
        link.use_count = 1
    elif state == "trainer_deactivated":
        trainer.status = UserStatus.inactive
    await db_session.flush()

    r = await client.get(f"{PREVIEW}/{code_}")
    assert (r.status_code, code(r)) == (404, "SHARE_LINK_INVALID")
    events = (await db_session.execute(select(ShareLinkEvent))).scalars().all()
    assert events == []  # only a valid link records an open


async def test_preview_expiry_boundary_is_exclusive(
    client: AsyncClient, make_trainer: MakeUser, add_share_link: AddShareLink, clock: FakeClock
) -> None:
    link = await add_share_link(
        await make_trainer(), kind="coach_unique", target_email="c@example.com"
    )
    clock.advance(days=7, seconds=-1)
    assert (await client.get(f"{PREVIEW}/{link.code}")).status_code == 200
    clock.advance(seconds=1)  # expires_at <= now is invalid, like redemption
    assert (await client.get(f"{PREVIEW}/{link.code}")).status_code == 404


async def test_preview_rate_limit_60_per_minute(client: AsyncClient) -> None:
    for _ in range(60):
        r = await client.get(f"{PREVIEW}/nope")
        assert r.status_code == 404
    limited = await client.get(f"{PREVIEW}/nope")
    assert (limited.status_code, code(limited)) == (429, "RATE_LIMITED")
    assert int(limited.headers["Retry-After"]) >= 1


async def test_public_branding_exposes_only_allowed_fields_with_cache_control(
    client: AsyncClient, make_trainer: MakeUser, db_session: AsyncSession
) -> None:
    await named_trainer(db_session, make_trainer)
    r = await client.get(f"{BRANDING}/acme-padel/branding")
    assert r.status_code == 200
    assert r.headers["Cache-Control"] == "public, max-age=300"
    assert set(r.json()) == {"business_name", "logo_url", "primary_color", "secondary_color"}
    assert r.json() == {
        "business_name": "Acme Padel",
        "logo_url": "https://media.example.com/logos/abc",
        "primary_color": "#112233",
        "secondary_color": "#AABBCC",
    }


async def test_public_branding_unknown_or_deactivated_404(
    client: AsyncClient, make_trainer: MakeUser, db_session: AsyncSession
) -> None:
    trainer = await named_trainer(db_session, make_trainer)
    missing = await client.get(f"{BRANDING}/no-such-slug/branding")
    assert (missing.status_code, code(missing)) == (404, "NOT_FOUND")
    assert "Cache-Control" not in missing.headers

    for status in (UserStatus.inactive, UserStatus.pending):
        trainer.status = status
        await db_session.flush()
        r = await client.get(f"{BRANDING}/acme-padel/branding")
        assert (r.status_code, code(r)) == (404, "NOT_FOUND")


async def test_public_branding_default_limit_keys_by_ip(
    client: AsyncClient, make_trainer: MakeUser, db_session: AsyncSession
) -> None:
    await named_trainer(db_session, make_trainer)
    for _ in range(30):  # the test default limit
        assert (await client.get(f"{BRANDING}/acme-padel/branding")).status_code == 200
    r = await client.get(f"{BRANDING}/acme-padel/branding")
    assert (r.status_code, code(r)) == (429, "RATE_LIMITED")


async def test_public_routes_need_no_token(
    client: AsyncClient,
    make_trainer: MakeUser,
    add_share_link: AddShareLink,
    db_session: AsyncSession,
) -> None:
    trainer = await named_trainer(db_session, make_trainer)
    link = await add_share_link(trainer)
    assert "Authorization" not in client.headers
    assert (await client.get(f"{PREVIEW}/{link.code}")).status_code == 200
    assert (await client.get(f"{BRANDING}/acme-padel/branding")).status_code == 200
    junk = {"Authorization": "Bearer not-a-token"}
    assert (await client.get(f"{PREVIEW}/{link.code}", headers=junk)).status_code == 200
