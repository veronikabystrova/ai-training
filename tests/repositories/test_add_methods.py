"""``add()`` on each repository (the factories build rows directly, bypassing them)."""

from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import new_id
from app.models.identity import (
    CoachProfile,
    PlayerProfile,
    Profile,
    TrainerProfile,
    User,
    UserRole,
)
from app.repositories.coach_profile import CoachProfileRepository
from app.repositories.player import PlayerProfileRepository
from app.repositories.profile import ProfileRepository, TrainerProfileRepository
from app.repositories.user import UserRepository
from tests.factories import MakeUser
from tests.fakes import FakeClock


async def test_profile_and_trainer_profile_add(db_session: AsyncSession) -> None:
    user = await UserRepository(db_session).add(
        User(
            id=new_id(),
            email="added-trainer@example.com",
            password_hash="x",
            role=UserRole.trainer,
        )
    )
    profile = await ProfileRepository(db_session).add(
        Profile(user_id=user.id, first_name="Ada", last_name="Lovelace")
    )
    trainer = await TrainerProfileRepository(db_session).add(
        TrainerProfile(user_id=user.id, business_name="Added Academy", slug="added-academy")
    )
    assert await ProfileRepository(db_session).get(user.id) is profile
    assert await TrainerProfileRepository(db_session).get_by_slug("added-academy") is trainer


async def test_coach_and_player_add(
    db_session: AsyncSession, make_trainer: MakeUser, make_user: MakeUser, clock: FakeClock
) -> None:
    trainer = await make_trainer()
    coach_user = await make_user(role=UserRole.coach)
    tenure = await CoachProfileRepository(db_session).add(
        CoachProfile(user_id=coach_user.id, trainer_id=trainer.id, joined_at=clock.now())
    )
    assert tenure.is_active is True
    active = await CoachProfileRepository(db_session).get_active(
        trainer_id=trainer.id, user_id=coach_user.id
    )
    assert active is tenure

    owner = await make_user()
    player = await PlayerProfileRepository(db_session).add(
        PlayerProfile(
            owner_user_id=owner.id,
            display_name="Added Player",
            birth_date=date(2012, 5, 1),
            gender="undisclosed",
        )
    )
    assert await PlayerProfileRepository(db_session).get(player.id) is player
