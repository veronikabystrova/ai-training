"""Identity & tenancy ORM models (requirements "Models" table, architecture section 3).

All foreign keys are ``ON DELETE RESTRICT``. Repositories never commit.
"""

from datetime import date, datetime, time
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import (
    CHAR,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    LargeBinary,
    SmallInteger,
    String,
    Text,
    Time,
    UniqueConstraint,
    false,
    func,
    text,
    true,
)
from sqlalchemy import CheckConstraint as Check
from sqlalchemy.dialects.postgresql import CITEXT, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.schema import conv

from app.db.base import Base, IdMixin, TimestampMixin


class UserRole(StrEnum):
    super_admin = "super_admin"
    trainer = "trainer"
    coach = "coach"
    player = "player"


class UserStatus(StrEnum):
    pending = "pending"
    active = "active"
    inactive = "inactive"
    deleted = "deleted"


class OttPurpose(StrEnum):
    trainer_setup = "trainer_setup"
    password_reset = "password_reset"  # noqa: S105  (enum value, not a credential)
    email_verify = "email_verify"
    email_change = "email_change"


class ShareLinkKind(StrEnum):
    player_static = "player_static"
    coach_unique = "coach_unique"


class LinkSource(StrEnum):
    share_link = "share_link"
    parent_added = "parent_added"
    camp_submission = "camp_submission"


class LinkStatus(StrEnum):
    active = "active"
    inactive = "inactive"


class Gender(StrEnum):
    female = "female"
    male = "male"
    other = "other"
    undisclosed = "undisclosed"


class ApprovalStatus(StrEnum):
    pending = "pending"
    approved = "approved"
    denied = "denied"
    expired = "expired"


ShareLinkEventKind = Literal["opened", "registered"]
SubjectType = Literal["player", "coach"]


def _enum(cls: type[StrEnum], name: str) -> Enum:
    # Types are created by the migration, never by metadata.create_all.
    return Enum(cls, name=name, values_callable=lambda e: [m.value for m in e], create_type=False)


def _tstz() -> DateTime:
    return DateTime(timezone=True)


def _fk(target: str) -> ForeignKey:
    return ForeignKey(target, ondelete="RESTRICT")


class User(IdMixin, TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("email", name="uq_users_email"),
        Index("ix_users_created_at_id", "created_at", "id"),
        Index(
            "ix_users_email_trgm",
            "email",
            postgresql_using="gin",
            postgresql_ops={"email": "gin_trgm_ops"},
        ),
    )

    email: Mapped[str] = mapped_column(CITEXT())
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(_enum(UserRole, "user_role"))
    status: Mapped[UserStatus] = mapped_column(
        _enum(UserStatus, "user_status"), server_default=UserStatus.pending.value
    )
    is_email_verified: Mapped[bool] = mapped_column(server_default=false())
    must_change_password: Mapped[bool] = mapped_column(server_default=false())
    last_login_at: Mapped[datetime | None] = mapped_column(_tstz())
    deleted_at: Mapped[datetime | None] = mapped_column(_tstz())

    profile: Mapped["Profile"] = relationship(back_populates="user", uselist=False, lazy="raise")


class Profile(Base):
    __tablename__ = "profiles"
    __table_args__ = (
        Index(
            "ix_profiles_full_name_trgm",
            text("(first_name || ' ' || last_name)"),
            postgresql_using="gin",
            postgresql_ops={"(first_name || ' ' || last_name)": "gin_trgm_ops"},
        ),
    )

    user_id: Mapped[UUID] = mapped_column(_fk("users.id"), primary_key=True)
    first_name: Mapped[str] = mapped_column(String(100))
    last_name: Mapped[str] = mapped_column(String(100))
    phone: Mapped[str | None] = mapped_column(String(32))
    avatar_key: Mapped[str | None] = mapped_column(String(255))
    school: Mapped[str | None] = mapped_column(String(255))
    timezone: Mapped[str] = mapped_column(String(64), server_default="UTC")

    user: Mapped[User] = relationship(back_populates="profile", lazy="raise")


class TrainerProfile(Base):
    __tablename__ = "trainer_profiles"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_trainer_profiles_slug"),
        Check(
            "(brand_primary_color IS NULL OR brand_primary_color ~ '^#[0-9A-Fa-f]{6}$') AND "
            "(brand_secondary_color IS NULL OR brand_secondary_color ~ '^#[0-9A-Fa-f]{6}$')",
            name=conv("ck_trainer_profiles_brand_hex"),
        ),
    )

    user_id: Mapped[UUID] = mapped_column(_fk("users.id"), primary_key=True)
    business_name: Mapped[str] = mapped_column(String(120))
    slug: Mapped[str] = mapped_column(String(64))
    address: Mapped[str | None] = mapped_column(String(255))
    website: Mapped[str | None] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    logo_key: Mapped[str | None] = mapped_column(String(255))
    brand_primary_color: Mapped[str | None] = mapped_column(CHAR(7))
    brand_secondary_color: Mapped[str | None] = mapped_column(CHAR(7))


class CoachProfile(IdMixin, Base):
    """One row per coach-at-trainer tenure (architecture 3.1)."""

    __tablename__ = "coach_profiles"
    __table_args__ = (
        Check("is_active = (left_at IS NULL)", name=conv("ck_coach_left_iff_inactive")),
        Index(
            "uq_coach_one_active_trainer",
            "user_id",
            unique=True,
            postgresql_where=text("is_active"),
        ),
        Index("ix_coach_trainer_active", "trainer_id", postgresql_where=text("is_active")),
    )

    user: Mapped[User] = relationship(lazy="raise")
    user_id: Mapped[UUID] = mapped_column(_fk("users.id"))
    trainer_id: Mapped[UUID] = mapped_column(_fk("trainer_profiles.user_id"))
    bio: Mapped[str | None] = mapped_column(Text)
    credentials: Mapped[str | None] = mapped_column(Text)
    certifications: Mapped[list[Any]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    is_public: Mapped[bool] = mapped_column(server_default=false())
    joined_at: Mapped[datetime] = mapped_column(_tstz())
    left_at: Mapped[datetime | None] = mapped_column(_tstz())
    is_active: Mapped[bool] = mapped_column(server_default=true())


class PlayerProfile(IdMixin, TimestampMixin, Base):
    """Carries no trainer_id (G-10): tenancy goes through ``trainer_player_links``."""

    __tablename__ = "player_profiles"
    __table_args__ = (
        Index("ix_player_profiles_owner_user_id", "owner_user_id"),
        Index("ix_player_profiles_login_user_id", "login_user_id"),
    )

    owner_user_id: Mapped[UUID] = mapped_column(_fk("users.id"))
    login_user_id: Mapped[UUID | None] = mapped_column(_fk("users.id"))
    display_name: Mapped[str] = mapped_column(String(120))
    birth_date: Mapped[date]
    gender: Mapped[Gender] = mapped_column(_enum(Gender, "gender"))
    skill_level: Mapped[str | None] = mapped_column(String(50))
    school: Mapped[str | None] = mapped_column(String(255))
    jersey_number: Mapped[int | None] = mapped_column(SmallInteger)
    is_dependent: Mapped[bool] = mapped_column(server_default=false())
    emergency_contact_name: Mapped[str | None] = mapped_column(String(120))
    emergency_contact_phone: Mapped[str | None] = mapped_column(String(32))
    token_spend_requires_approval: Mapped[bool] = mapped_column(server_default=true())


class ShareLink(IdMixin, Base):
    __tablename__ = "share_links"
    __table_args__ = (
        UniqueConstraint("code", name="uq_share_links_code"),
        Check(
            "(kind = 'coach_unique' AND target_email IS NOT NULL AND max_uses = 1 "
            "AND expires_at IS NOT NULL) OR (kind = 'player_static' AND target_email IS NULL)",
            name=conv("ck_share_links_kind_fields"),
        ),
        Index("ix_share_links_trainer_id", "trainer_id"),
    )

    code: Mapped[str] = mapped_column(String(64))
    trainer_id: Mapped[UUID] = mapped_column(_fk("trainer_profiles.user_id"))
    created_by_user_id: Mapped[UUID] = mapped_column(_fk("users.id"))
    kind: Mapped[ShareLinkKind] = mapped_column(_enum(ShareLinkKind, "share_link_kind"))
    target_email: Mapped[str | None] = mapped_column(CITEXT())
    expires_at: Mapped[datetime | None] = mapped_column(_tstz())
    max_uses: Mapped[int | None]
    use_count: Mapped[int] = mapped_column(server_default="0")
    is_active: Mapped[bool] = mapped_column(server_default=true())
    created_at: Mapped[datetime] = mapped_column(_tstz(), server_default=func.now())


class TrainerPlayerLink(IdMixin, Base):
    __tablename__ = "trainer_player_links"
    __table_args__ = (
        UniqueConstraint(
            "trainer_id", "player_profile_id", name="uq_trainer_player_links_trainer_player"
        ),
        Index("ix_trainer_player_links_trainer_status", "trainer_id", "status"),
        Index("ix_trainer_player_links_player_profile_id", "player_profile_id"),
    )

    trainer_id: Mapped[UUID] = mapped_column(_fk("trainer_profiles.user_id"))
    player_profile_id: Mapped[UUID] = mapped_column(_fk("player_profiles.id"))
    source: Mapped[LinkSource] = mapped_column(_enum(LinkSource, "link_source"))
    share_link_id: Mapped[UUID | None] = mapped_column(_fk("share_links.id"))
    linked_at: Mapped[datetime] = mapped_column(_tstz(), server_default=func.now())
    status: Mapped[LinkStatus] = mapped_column(
        _enum(LinkStatus, "link_status"), server_default=LinkStatus.active.value
    )


class ShareLinkEvent(IdMixin, Base):
    __tablename__ = "share_link_events"
    __table_args__ = (
        Check("kind IN ('opened', 'registered')", name=conv("ck_share_link_events_kind")),
        Index("ix_share_link_events_share_link_id", "share_link_id"),
    )

    share_link_id: Mapped[UUID] = mapped_column(_fk("share_links.id"))
    kind: Mapped[ShareLinkEventKind] = mapped_column(String(16))
    occurred_at: Mapped[datetime] = mapped_column(_tstz(), server_default=func.now())
    user_agent_hash: Mapped[str | None] = mapped_column(String(64))
    ip_hash: Mapped[str | None] = mapped_column(String(64))


class AvailabilityWindow(IdMixin, Base):
    """The GiST EXCLUDE ``ex_availability_no_overlap`` lives in the migration (raw DDL)."""

    __tablename__ = "availability_windows"
    __table_args__ = (
        Check("weekday BETWEEN 0 AND 6", name=conv("ck_availability_windows_weekday")),
        Check("start_time < end_time", name=conv("ck_availability_start_before_end")),
        Check(
            "subject_type IN ('player', 'coach')",
            name=conv("ck_availability_windows_subject_type"),
        ),
        Index("ix_availability_windows_subject", "subject_type", "subject_id"),
    )

    subject_type: Mapped[SubjectType] = mapped_column(String(16))
    subject_id: Mapped[UUID]
    weekday: Mapped[int] = mapped_column(SmallInteger)
    start_time: Mapped[time] = mapped_column(Time())
    end_time: Mapped[time] = mapped_column(Time())
    is_available: Mapped[bool] = mapped_column(server_default=true())


class PurchaseApproval(IdMixin, Base):
    __tablename__ = "purchase_approvals"
    __table_args__ = (
        Index("ix_purchase_approvals_status_expires_at", "status", "expires_at"),
        Index("ix_purchase_approvals_parent_user_id", "parent_user_id"),
    )

    player_profile_id: Mapped[UUID] = mapped_column(_fk("player_profiles.id"))
    parent_user_id: Mapped[UUID] = mapped_column(_fk("users.id"))
    trainer_id: Mapped[UUID] = mapped_column(_fk("trainer_profiles.user_id"))
    subject_type: Mapped[str] = mapped_column(String(32))
    subject_id: Mapped[UUID]
    amount_cents: Mapped[int | None]
    token_amount: Mapped[int | None]
    status: Mapped[ApprovalStatus] = mapped_column(
        _enum(ApprovalStatus, "approval_status"), server_default=ApprovalStatus.pending.value
    )
    requested_at: Mapped[datetime] = mapped_column(_tstz(), server_default=func.now())
    responded_at: Mapped[datetime | None] = mapped_column(_tstz())
    expires_at: Mapped[datetime] = mapped_column(_tstz())
    parent_note: Mapped[str | None] = mapped_column(String(500))


class ImpersonationSession(IdMixin, Base):
    __tablename__ = "impersonation_sessions"
    __table_args__ = (UniqueConstraint("jti", name="uq_impersonation_sessions_jti"),)

    admin_user_id: Mapped[UUID] = mapped_column(_fk("users.id"))
    target_user_id: Mapped[UUID] = mapped_column(_fk("users.id"))
    started_at: Mapped[datetime] = mapped_column(_tstz(), server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(_tstz())
    expires_at: Mapped[datetime] = mapped_column(_tstz())
    reason: Mapped[str | None] = mapped_column(String(500))
    jti: Mapped[UUID]


class UserErasure(IdMixin, Base):
    """Erasure ledger: ``original_user_id`` deliberately has no FK."""

    __tablename__ = "user_erasures"

    original_user_id: Mapped[UUID]
    original_email_hash: Mapped[str] = mapped_column(String(64))
    erased_by_user_id: Mapped[UUID] = mapped_column(_fk("users.id"))
    reason: Mapped[str | None] = mapped_column(String(500))
    erased_at: Mapped[datetime] = mapped_column(_tstz(), server_default=func.now())
    legal_retention_until: Mapped[datetime] = mapped_column(_tstz())


class RefreshToken(IdMixin, Base):
    __tablename__ = "refresh_tokens"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_refresh_tokens_token_hash"),
        Index("ix_refresh_tokens_user_id", "user_id"),
        Index(
            "ix_refresh_tokens_family_live",
            "family_id",
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    user_id: Mapped[UUID] = mapped_column(_fk("users.id"))
    family_id: Mapped[UUID]
    token_hash: Mapped[bytes] = mapped_column(LargeBinary)
    expires_at: Mapped[datetime] = mapped_column(_tstz())
    consumed_at: Mapped[datetime | None] = mapped_column(_tstz())
    revoked_at: Mapped[datetime | None] = mapped_column(_tstz())
    user_agent_hash: Mapped[str | None] = mapped_column(String(64))
    ip_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(_tstz(), server_default=func.now())


class OneTimeToken(IdMixin, Base):
    __tablename__ = "one_time_tokens"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_ott_token_hash"),
        Index(
            "ix_ott_user_purpose_open",
            "user_id",
            "purpose",
            postgresql_where=text("used_at IS NULL"),
        ),
        Index("ix_ott_expires", "expires_at"),
    )

    user_id: Mapped[UUID] = mapped_column(_fk("users.id"))
    purpose: Mapped[OttPurpose] = mapped_column(_enum(OttPurpose, "ott_purpose"))
    token_hash: Mapped[bytes] = mapped_column(LargeBinary)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    expires_at: Mapped[datetime] = mapped_column(_tstz())
    used_at: Mapped[datetime | None] = mapped_column(_tstz())
    created_at: Mapped[datetime] = mapped_column(_tstz(), server_default=func.now())
