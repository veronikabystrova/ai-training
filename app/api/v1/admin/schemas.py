from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

from app.api.v1.common import Role, SessionUser
from app.api.v1.me.schemas import Timezone
from app.models.identity import UserStatus
from app.services.admin_user_service import AdminUserView, NewTrainer
from app.services.impersonation_service import StartedImpersonation

# Documented as an e-mail address, but not validated on the way out: an erased account keeps
# ``deleted-<id>@erased.invalid``, which a strict validator rejects as a reserved domain.
StoredEmail = Annotated[str, Field(json_schema_extra={"format": "email"})]


class AdminUserSummary(BaseModel):
    id: UUID
    email: StoredEmail
    role: Role
    status: UserStatus
    first_name: str
    last_name: str
    trainer_id: UUID | None
    created_at: datetime
    last_login_at: datetime | None


class AdminUserDetail(AdminUserSummary):
    phone: str | None
    school: str | None
    timezone: str
    email_verified: bool
    must_change_password: bool
    deleted_at: datetime | None
    business_name: str | None  # trainers only
    slug: str | None  # trainers only


def summary_response(view: AdminUserView) -> AdminUserSummary:
    u, p = view.user, view.profile
    return AdminUserSummary(
        id=u.id,
        email=u.email,
        role=u.role,
        status=u.status,
        first_name=p.first_name,
        last_name=p.last_name,
        trainer_id=view.trainer_id,
        created_at=u.created_at,
        last_login_at=u.last_login_at,
    )


def detail_response(view: AdminUserView) -> AdminUserDetail:
    u, p = view.user, view.profile
    return AdminUserDetail(
        **summary_response(view).model_dump(),
        phone=p.phone,
        school=p.school,
        timezone=p.timezone,
        email_verified=u.is_email_verified,
        must_change_password=u.must_change_password,
        deleted_at=u.deleted_at,
        business_name=view.business_name,
        slug=view.slug,
    )


class ProvisionTrainer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr = Field(max_length=255)
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    phone: str | None = Field(default=None, max_length=32)
    business_name: str = Field(min_length=1, max_length=120)
    slug: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", min_length=3, max_length=60)
    timezone: Timezone = "UTC"

    def to_input(self) -> NewTrainer:
        return NewTrainer(
            email=self.email,
            first_name=self.first_name,
            last_name=self.last_name,
            phone=self.phone,
            business_name=self.business_name,
            slug=self.slug,
            timezone=self.timezone,
        )


class AdminUpdateUser(BaseModel):
    """Role and email are immutable here: ``extra="forbid"`` rejects them."""

    model_config = ConfigDict(extra="forbid")

    first_name: str | None = Field(default=None, min_length=1, max_length=100)
    last_name: str | None = Field(default=None, min_length=1, max_length=100)
    phone: str | None = Field(default=None, max_length=32)
    timezone: Timezone | None = None

    @model_validator(mode="after")
    def _no_null_for_required_fields(self) -> "AdminUpdateUser":
        for name in ("first_name", "last_name", "timezone"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self

    def changes(self) -> dict[str, Any]:
        """Only the fields the client sent (``model_fields_set``)."""
        return self.model_dump(exclude_unset=True)


class ImpersonationResponse(BaseModel):
    access_token: str  # sub=target, act=admin, exp <= 60 min
    refresh_token: str
    expires_at: datetime
    target: SessionUser


def impersonation_response(started: StartedImpersonation) -> ImpersonationResponse:
    user = started.target
    return ImpersonationResponse(
        access_token=started.access_token,
        refresh_token=started.refresh_token,
        expires_at=started.expires_at,
        target=SessionUser(
            id=user.id,
            role=user.role,
            email=user.email,
            email_verified=user.is_email_verified,
            must_change_password=user.must_change_password,
            trainer_id=started.trainer_id,
            child=started.child,
        ),
    )
