"""Schemas shared by several domains (api-designer-spec "Shared schemas")."""

from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.identity import UserRole as Role

__all__ = [
    "AuthResponse",
    "Password",
    "Role",
    "SessionUser",
    "StoredEmail",
    "TokenPair",
    "iana_timezone",
]

# Documented as an e-mail address, but not validated on the way out: an erased account keeps
# ``deleted-<id>@erased.invalid``, which a strict validator rejects as a reserved domain.
StoredEmail = Annotated[str, Field(json_schema_extra={"format": "email"})]

# zxcvbn >= 3 is enforced by ``PasswordPolicy`` (422 WEAK_PASSWORD), not by the schema.
Password = Annotated[str, Field(min_length=12, max_length=128)]


def iana_timezone(value: str) -> str:
    """Pydantic validator body: the value must be an IANA zone known to ``zoneinfo``."""
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("must be an IANA timezone name") from exc
    return value


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
