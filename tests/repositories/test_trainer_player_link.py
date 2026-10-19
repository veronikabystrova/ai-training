from sqlalchemy.ext.asyncio import AsyncSession

from app.models.identity import LinkSource, LinkStatus
from app.repositories.trainer_player_link import TrainerPlayerLinkRepository
from tests.factories import MakePlayer, MakeUser


async def test_upsert_active_is_idempotent(
    db_session: AsyncSession, make_trainer: MakeUser, make_player: MakePlayer
) -> None:
    repo = TrainerPlayerLinkRepository(db_session)
    t = await make_trainer()
    p = await make_player()
    first = await repo.upsert_active(
        trainer_id=t.id, player_profile_id=p.id, source=LinkSource.share_link, share_link_id=None
    )
    second = await repo.upsert_active(
        trainer_id=t.id, player_profile_id=p.id, source=LinkSource.parent_added, share_link_id=None
    )
    assert first.id == second.id
    assert second.source is LinkSource.share_link  # original provenance is kept
    assert len(await repo.list_for_trainer(trainer_id=t.id)) == 1


async def test_deactivate_then_upsert_reactivates(
    db_session: AsyncSession, make_trainer: MakeUser, make_player: MakePlayer
) -> None:
    repo = TrainerPlayerLinkRepository(db_session)
    t = await make_trainer()
    p = await make_player()
    link = await repo.upsert_active(
        trainer_id=t.id, player_profile_id=p.id, source=LinkSource.share_link, share_link_id=None
    )
    assert await repo.deactivate(trainer_id=t.id, player_profile_id=p.id) is True
    assert await repo.deactivate(trainer_id=t.id, player_profile_id=p.id) is False
    assert await repo.list_for_trainer(trainer_id=t.id) == []
    again = await repo.upsert_active(
        trainer_id=t.id, player_profile_id=p.id, source=LinkSource.share_link, share_link_id=None
    )
    assert again.id == link.id and again.status is LinkStatus.active


async def test_reads_never_leak_other_tenants(
    db_session: AsyncSession, make_trainer: MakeUser, make_player: MakePlayer
) -> None:
    repo = TrainerPlayerLinkRepository(db_session)
    t1, t2 = await make_trainer(), await make_trainer()
    p = await make_player()
    await repo.upsert_active(
        trainer_id=t1.id, player_profile_id=p.id, source=LinkSource.share_link, share_link_id=None
    )
    assert await repo.get(trainer_id=t2.id, player_profile_id=p.id) is None
    assert await repo.list_for_trainer(trainer_id=t2.id) == []
    assert await repo.deactivate(trainer_id=t2.id, player_profile_id=p.id) is False
    assert await repo.has_active_link(trainer_id=t1.id, player_profile_id=p.id) is True
    assert await repo.has_active_link(trainer_id=t2.id, player_profile_id=p.id) is False


async def test_activate_reports_whether_anything_changed(
    db_session: AsyncSession, make_trainer: MakeUser, make_player: MakePlayer
) -> None:
    repo = TrainerPlayerLinkRepository(db_session)
    t = await make_trainer()
    p = await make_player()

    async def activate() -> bool:
        return await repo.activate(
            trainer_id=t.id,
            player_profile_id=p.id,
            source=LinkSource.share_link,
            share_link_id=None,
        )

    assert await activate() is True  # inserted
    assert await activate() is False  # already active
    assert await repo.deactivate(trainer_id=t.id, player_profile_id=p.id) is True
    assert await activate() is True  # reactivated
    assert len(await repo.list_for_trainer(trainer_id=t.id)) == 1
