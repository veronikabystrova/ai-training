from datetime import date, datetime, time
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    HttpUrl,
    PlainSerializer,
    model_validator,
)

from app.api.deps import Principal
from app.api.v1.common import Password, Role, iana_timezone
from app.models.identity import Gender, LinkSource, LinkStatus
from app.repositories.availability import WindowSpec
from app.services.availability_service import AvailabilityView
from app.services.player_service import (
    ContextView,
    LoginInput,
    NewPlayer,
    PlayerView,
    TrainerLinkView,
)
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


def _minute_precision(value: time) -> time:
    if value.tzinfo is not None or value.second or value.microsecond:
        raise ValueError("must be a wall-clock time with minute precision (HH:MM)")
    return value


WallClock = Annotated[
    time, AfterValidator(_minute_precision), PlainSerializer(lambda t: t.strftime("%H:%M"))
]


class WeeklyWindow(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    weekday: int = Field(ge=0, le=6)  # 0 = Monday
    start_time: WallClock
    end_time: WallClock

    @model_validator(mode="after")
    def _start_before_end(self) -> Self:
        if self.start_time >= self.end_time:
            raise ValueError("start_time must be before end_time")
        return self


class AvailabilitySet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timezone: Timezone
    windows: list[WeeklyWindow] = Field(max_length=70)

    def specs(self) -> list[WindowSpec]:
        return [
            WindowSpec(weekday=w.weekday, start_time=w.start_time, end_time=w.end_time)
            for w in self.windows
        ]


def availability_response(view: AvailabilityView) -> AvailabilitySet:
    return AvailabilitySet(
        timezone=view.timezone,
        windows=[WeeklyWindow.model_validate(w) for w in view.windows],
    )


# --- dependents (/me/players) and contexts ------------------------------------------------


class ChildLogin(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr = Field(max_length=255)
    password: Password

    def to_input(self) -> LoginInput:
        return LoginInput(email=self.email, password=self.password)


class CreatePlayer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=1, max_length=100)
    birth_date: date  # 1 <= age < 18 on Clock.today(), else 422 INVALID_AGE
    gender: Gender
    skill_level: str | None = Field(default=None, max_length=50)
    trainer_ids: list[UUID] = Field(default=[], max_length=20)  # already linked to the parent
    token_spend_requires_approval: bool = False
    login: ChildLogin | None = None

    def to_input(self) -> NewPlayer:
        return NewPlayer(
            display_name=self.display_name,
            birth_date=self.birth_date,
            gender=self.gender,
            skill_level=self.skill_level,
            trainer_ids=self.trainer_ids,
            token_spend_requires_approval=self.token_spend_requires_approval,
            login=self.login.to_input() if self.login else None,
        )


_PLAYER_NOT_NULLABLE = ("display_name", "birth_date", "gender", "token_spend_requires_approval")


class UpdatePlayer(BaseModel):
    """All optional. ``login``: absent = unchanged, ``null`` = revoke, object = create/replace."""

    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    birth_date: date | None = None
    gender: Gender | None = None
    skill_level: str | None = Field(default=None, max_length=50)
    token_spend_requires_approval: bool | None = None
    login: ChildLogin | None = None  # distinguished with model_fields_set

    @model_validator(mode="after")
    def _no_null_for_required_fields(self) -> Self:
        for name in _PLAYER_NOT_NULLABLE:
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self

    def changes(self) -> dict[str, Any]:
        """Only the fields the client sent (``model_fields_set``)."""
        changes = {name: getattr(self, name) for name in self.model_fields_set}
        if "login" in changes and self.login is not None:
            changes["login"] = self.login.to_input()
        return changes


class AddTrainerLink(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trainer_id: UUID  # a trainer the parent is already linked to (source parent_added)


class TrainerLinkResponse(BaseModel):
    trainer_id: UUID
    business_name: str
    status: LinkStatus
    source: LinkSource
    linked_at: datetime


class PlayerResponse(BaseModel):
    id: UUID
    display_name: str
    birth_date: date
    gender: Gender
    skill_level: str | None
    is_dependent: bool
    token_spend_requires_approval: bool
    has_login: bool  # login_user_id set and that user active
    trainers: list[TrainerLinkResponse]


def trainer_link_response(link: TrainerLinkView) -> TrainerLinkResponse:
    return TrainerLinkResponse(
        trainer_id=link.trainer_id,
        business_name=link.business_name,
        status=link.status,
        source=link.source,
        linked_at=link.linked_at,
    )


def player_response(view: PlayerView) -> PlayerResponse:
    p = view.profile
    return PlayerResponse(
        id=p.id,
        display_name=p.display_name,
        birth_date=p.birth_date,
        gender=p.gender,
        skill_level=p.skill_level,
        is_dependent=p.is_dependent,
        token_spend_requires_approval=p.token_spend_requires_approval,
        has_login=view.has_login,
        trainers=[trainer_link_response(t) for t in view.trainers],
    )


class PlayerContext(BaseModel):
    trainer_id: UUID
    business_name: str
    logo_url: str | None
    player_profile_ids: list[UUID]  # profiles the caller may act for under this trainer
    linked_at: datetime


def context_response(view: ContextView) -> PlayerContext:
    return PlayerContext(
        trainer_id=view.trainer_id,
        business_name=view.business_name,
        logo_url=view.logo_url,
        player_profile_ids=view.player_profile_ids,
        linked_at=view.linked_at,
    )
