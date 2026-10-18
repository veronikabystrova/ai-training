from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.api.v1.common import AuthResponse, SessionUser, TokenPair
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

    refresh_token: str  # identifies the family; must belong to the caller


def token_pair(tokens: TokenSet) -> TokenPair:
    return TokenPair(
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
        expires_in=tokens.expires_in,
    )


def auth_response(issued: IssuedSession) -> AuthResponse:
    user = issued.user
    return AuthResponse(
        tokens=token_pair(issued.tokens),
        user=SessionUser(
            id=user.id,
            role=user.role,
            email=user.email,
            email_verified=user.is_email_verified,
            must_change_password=user.must_change_password,
            trainer_id=issued.trainer_id,
            child=issued.child,
        ),
    )
