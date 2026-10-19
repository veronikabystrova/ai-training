from typing import Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    HttpUrl,
    model_validator,
)

from app.api.deps import Principal
from app.api.v1.common import Role, iana_timezone
from app.services.user_service import AVATAR_MAX_BYTES, MeView

Timezone = Annotated[str, Field(max_length=64), AfterValidator(iana_timezone)]


class Certification(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    issuer: str | None = Field(default=None, max_length=200)
    year: int | None = Field(default=None, ge=1950, le=2100)


class CoachPublicFields(BaseModel):
    bio: str | None = Field(default=None, max_length=2000)
    credentials: str | None = Field(default=None, max_length=2000)
    certifications: list[Certification] = Field(default=[], max_length=50)
    is_public: bool = False


class MeResponse(BaseModel):
    id: UUID
    role: Role
    email: EmailStr
    email_verified: bool
    must_change_password: bool
    first_name: str
    last_name: str
    phone: str | None
    school: str | None
    timezone: str
    avatar_url: str | None
    child: bool
    impersonated_by: UUID | None  # Principal.act
    coach: CoachPublicFields | None  # role == coach only
    trainer_id: UUID | None


def me_response(view: MeView, principal: Principal) -> MeResponse:
    coach = view.coach
    return MeResponse(
        id=view.user.id,
        role=view.user.role,
        email=view.user.email,
        email_verified=view.user.is_email_verified,
        must_change_password=view.user.must_change_password,
        first_name=view.profile.first_name,
        last_name=view.profile.last_name,
        phone=view.profile.phone,
        school=view.profile.school,
        timezone=view.profile.timezone,
        avatar_url=view.avatar_url,
        child=principal.child,
        impersonated_by=principal.act,
        coach=None
        if coach is None
        else CoachPublicFields(
            bio=coach.bio,
            credentials=coach.credentials,
            certifications=[Certification.model_validate(c) for c in coach.certifications],
            is_public=coach.is_public,
        ),
        trainer_id=principal.trainer_id,
    )


# Columns that cannot be NULL: an explicit ``null`` is a validation error, not a no-op.
_NOT_NULLABLE = ("first_name", "last_name", "timezone", "certifications", "is_public")


class UpdateMe(BaseModel):
    """Email, role and status are not editable (``extra="forbid"`` rejects them)."""

    model_config = ConfigDict(extra="forbid")

    first_name: str | None = Field(default=None, min_length=1, max_length=100)
    last_name: str | None = Field(default=None, min_length=1, max_length=100)
    phone: str | None = Field(default=None, max_length=32)
    school: str | None = Field(default=None, max_length=200)
    timezone: Timezone | None = None
    # coach-only (any other role sending these => 422 FIELD_NOT_ALLOWED, FR-208)
    bio: str | None = Field(default=None, max_length=2000)
    credentials: str | None = Field(default=None, max_length=2000)
    certifications: list[Certification] | None = Field(default=None, max_length=50)
    is_public: bool | None = None

    @model_validator(mode="after")
    def _no_null_for_required_fields(self) -> Self:
        for name in _NOT_NULLABLE:
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self

    def changes(self) -> dict[str, Any]:
        """Only the fields the client sent (``model_fields_set``), as plain values."""
        return self.model_dump(exclude_unset=True)


class AvatarPresignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_type: Literal["image/jpeg", "image/png", "image/webp"]
    content_length: int = Field(gt=0, le=AVATAR_MAX_BYTES)


class PresignResponse(BaseModel):
    upload_url: HttpUrl  # presigned PUT, constrains type and length
    key: str  # server-chosen: avatars/{uuid7}
    expires_in: int  # seconds
    required_headers: dict[str, str]


class AvatarConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    avatar_key: str = Field(pattern=r"^avatars/[0-9a-f-]{36}$")


class EmailChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    new_email: EmailStr = Field(max_length=255)
    password: str = Field(min_length=1, max_length=128)  # re-auth


class EmailChangeConfirm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=20, max_length=200)
