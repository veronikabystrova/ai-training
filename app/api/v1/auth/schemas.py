from datetime import date
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.api.v1.common import AuthResponse, Password, SessionUser, TokenPair
from app.models.identity import User
from app.services.auth_service import IssuedSession, TokenSet


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class RefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    refresh_token: str = Field(min_length=20, max_length=200)


class LogoutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    refresh_token: str = Field(min_length=20, max_length=200)  # family id; must be the caller's


class PasswordResetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr


class PasswordResetConfirm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=20, max_length=200)
    new_password: Password


class VerifyEmailRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=20, max_length=200)


class PasswordSetupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=20, max_length=200)
    new_password: Password


class StatusResponse(BaseModel):
    status: Literal["accepted", "already_verified"]


def token_pair(tokens: TokenSet) -> TokenPair:
    return TokenPair(
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
        expires_in=tokens.expires_in,
    )


def session_user(user: User, *, trainer_id: UUID | None, child: bool) -> SessionUser:
    return SessionUser(
        id=user.id,
        role=user.role,
        email=user.email,
        email_verified=user.is_email_verified,
        must_change_password=user.must_change_password,
        trainer_id=trainer_id,
        child=child,
    )


def auth_response(issued: IssuedSession) -> AuthResponse:
    return AuthResponse(
        tokens=token_pair(issued.tokens),
        user=session_user(issued.user, trainer_id=issued.trainer_id, child=issued.child),
    )


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Identity fields are required only when a new account is created (the service answers 422
    # VALIDATION_ERROR); an authenticated redemption may send just ``player_profile_ids``.
    email: EmailStr | None = Field(default=None, max_length=255)
    password: Password | None = None
    first_name: str | None = Field(default=None, min_length=1, max_length=100)
    last_name: str | None = Field(default=None, min_length=1, max_length=100)
    phone: str | None = Field(default=None, max_length=32)
    timezone: str = Field(default="UTC", max_length=64)
    # Required for a new player account (checked against the link kind and the clock in the
    # service, 422); ignored for coach links and authenticated redemptions.
    birth_date: date | None = None
    # A parent redeeming a static link for existing dependents; ``[]`` is rejected.
    player_profile_ids: list[UUID] | None = Field(default=None, min_length=1)

    @field_validator("timezone")
    @classmethod
    def _iana_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("must be an IANA timezone name") from exc
        return value


class LinkedResponse(BaseModel):
    """``200`` for an authenticated existing user: linked to the trainer, no new account."""

    user: SessionUser
    linked_trainer_id: UUID
