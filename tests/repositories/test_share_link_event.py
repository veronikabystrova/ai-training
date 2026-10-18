import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.identity import ShareLinkEventKind
from app.repositories.share_link_event import ShareLinkEventRepository
from tests.factories import AddShareLink, MakeUser


@pytest.mark.parametrize("kind", ["opened", "registered"])
async def test_append_persists_event(
    db_session: AsyncSession,
    make_trainer: MakeUser,
    add_share_link: AddShareLink,
    kind: ShareLinkEventKind,
) -> None:
    link = await add_share_link(await make_trainer())
    event = await ShareLinkEventRepository(db_session).append(
        share_link_id=link.id, kind=kind, user_agent_hash="ua", ip_hash="ip"
    )
    await db_session.refresh(event)
    assert (event.kind, event.share_link_id, event.user_agent_hash, event.ip_hash) == (
        kind,
        link.id,
        "ua",
        "ip",
    )
    assert event.occurred_at is not None


async def test_append_rejects_unknown_kind(
    db_session: AsyncSession, make_trainer: MakeUser, add_share_link: AddShareLink
) -> None:
    link = await add_share_link(await make_trainer())
    bogus: ShareLinkEventKind = "bogus"  # type: ignore[assignment]
    with pytest.raises(IntegrityError, match="ck_share_link_events_kind"):
        await ShareLinkEventRepository(db_session).append(share_link_id=link.id, kind=bogus)
