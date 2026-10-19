import pytest

from app.models.identity import User, UserRole
from tests.factories import MakeUser, PrincipalToken


@pytest.fixture
async def admin(make_user: MakeUser) -> User:
    return await make_user(role=UserRole.super_admin, first_name="Ada", last_name="Admin")


@pytest.fixture
async def admin_token(admin: User, principal_token: PrincipalToken) -> str:
    return principal_token(admin)
