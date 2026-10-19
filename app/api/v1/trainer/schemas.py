from datetime import datetime
from typing import Any, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, HttpUrl, model_validator

from app.api.v1.me.schemas import AvailabilitySet, availability_response
from app.core.config import Settings
from app.models.identity import ShareLink, ShareLinkKind
from app.services.availability_service import SubjectAvailability
from app.services.branding_service import LOGO_MAX_BYTES, BrandingView


class CreateShareLink(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ShareLinkKind
    target_email: EmailStr | None = None  # required iff kind == coach_unique

    @model_validator(mode="after")
    def _target_email_iff_coach_unique(self) -> Self:
        if self.kind is ShareLinkKind.coach_unique and self.target_email is None:
            raise ValueError("target_email is required for coach_unique links")
        if self.kind is ShareLinkKind.player_static and self.target_email is not None:
            raise ValueError("target_email is only allowed for coach_unique links")
        return self


class ShareLinkResponse(BaseModel):
    id: UUID
    code: str
    kind: ShareLinkKind
    url: str
    target_email: EmailStr | None
    expires_at: datetime | None  # coach_unique: now + 7 d; static: null
    max_uses: int | None  # coach_unique: 1; static: null (unlimited)
    use_count: int
    is_active: bool
    created_at: datetime


def share_link_response(link: ShareLink, settings: Settings) -> ShareLinkResponse:
    return ShareLinkResponse(
        id=link.id,
        code=link.code,
        kind=link.kind,
        url=f"{settings.public_base_url.rstrip('/')}/join/{link.code}",
        target_email=link.target_email,
        expires_at=link.expires_at,
        max_uses=link.max_uses,
        use_count=link.use_count,
        is_active=link.is_active,
        created_at=link.created_at,
    )


class AvailabilityRead(AvailabilitySet):
    subject_id: UUID  # player_profile_id or coach user_id
    subject_type: Literal["player", "coach"]


def availability_read(subject: SubjectAvailability) -> AvailabilityRead:
    base = availability_response(subject.view)
    return AvailabilityRead(
        timezone=base.timezone,
        windows=base.windows,
        subject_id=subject.subject_id,
        subject_type=subject.subject_type,
    )


HEX_COLOR = r"^#[0-9A-Fa-f]{6}$"


class BrandingResponse(BaseModel):
    business_name: str
    logo_url: str | None
    primary_color: str | None  # #RRGGBB
    secondary_color: str | None


def branding_response(view: BrandingView) -> BrandingResponse:
    return BrandingResponse(
        business_name=view.business_name,
        logo_url=view.logo_url,
        primary_color=view.primary_color,
        secondary_color=view.secondary_color,
    )


class UpdateBranding(BaseModel):
    """Absent = unchanged; an explicit ``null`` clears a colour (the name cannot be cleared)."""

    model_config = ConfigDict(extra="forbid")

    business_name: str | None = Field(default=None, min_length=1, max_length=120)
    primary_color: str | None = Field(default=None, pattern=HEX_COLOR)
    secondary_color: str | None = Field(default=None, pattern=HEX_COLOR)

    @model_validator(mode="after")
    def _name_is_not_nullable(self) -> Self:
        if "business_name" in self.model_fields_set and self.business_name is None:
            raise ValueError("business_name cannot be null")
        return self

    def changes(self) -> dict[str, Any]:
        """Only the fields the client sent, keyed by the ``trainer_profiles`` column."""
        columns = {
            "business_name": "business_name",
            "primary_color": "brand_primary_color",
            "secondary_color": "brand_secondary_color",
        }
        return {columns[name]: getattr(self, name) for name in self.model_fields_set}


class LogoPresignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_type: Literal["image/png", "image/svg+xml", "image/webp"]
    content_length: int = Field(gt=0, le=LOGO_MAX_BYTES)


class LogoPresignResponse(BaseModel):
    upload_url: HttpUrl  # presigned PUT, constrains type and length
    key: str  # server-chosen, bound to the trainer: logos/{uuid}
    expires_in: int  # seconds
    required_headers: dict[str, str]


class LogoConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    logo_key: str = Field(pattern=r"^logos/[0-9a-f-]{36}$")
