"""Schemas shared by several domains (api-designer-spec "Shared schemas")."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.identity import UserRole as Role

__all__ = ["AuthResponse", "Password", "Role", "SessionUser", "TokenPair"]

# zxcvbn >= 3 is enforced by ``PasswordPolicy`` (422 WEAK_PASSWORD), not by the schema.
Password = Annotated[str, Field(min_length=12, max_length=128)]


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str  # opaque, 256-bit; only ever returned here
    token_type: Literal["bearer"] = "bearer"  # noqa: S105
    expires_in: int = 900  # access TTL seconds


class SessionUser(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    role: Role
    email: EmailStr
    email_verified: bool
    must_change_password: bool
    trainer_id: UUID | None  # trainer self / coach active tenant / None for player
    child: bool


class AuthResponse(BaseModel):
    tokens: TokenPair
    user: SessionUser
