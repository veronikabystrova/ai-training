from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, model_validator

from app.api.v1.me.schemas import AvailabilitySet, availability_response
from app.core.config import Settings
from app.models.identity import ShareLink, ShareLinkKind
from app.services.availability_service import SubjectAvailability


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
