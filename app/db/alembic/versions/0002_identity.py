"""identity: users, profiles, tenancy, tokens, availability, approvals

Revision ID: 0002
Revises: 0001
"""

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.schema import conv

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

_ENUMS: dict[str, tuple[str, ...]] = {
    "user_role": ("super_admin", "trainer", "coach", "player"),
    "user_status": ("pending", "active", "inactive", "deleted"),
    "ott_purpose": ("trainer_setup", "password_reset", "email_verify", "email_change"),
    "share_link_kind": ("player_static", "coach_unique"),
    "link_source": ("share_link", "parent_added", "camp_submission"),
    "link_status": ("active", "inactive"),
    "approval_status": ("pending", "approved", "denied", "expired"),
}


def _enum(name: str) -> pg.ENUM:
    return pg.ENUM(*_ENUMS[name], name=name, create_type=False)


def _fk(table: str, column: str, target: str) -> sa.ForeignKeyConstraint:
    ref_table, _ref_col = target.split(".")
    return sa.ForeignKeyConstraint(
        [column],
        [target],
        name=f"fk_{table}_{column}_{ref_table}",
        ondelete="RESTRICT",
    )


def _id() -> sa.Column[UUID]:
    return sa.Column("id", sa.Uuid(), nullable=False)


def _tstz(name: str, *, nullable: bool = True, now: bool = False) -> sa.Column[datetime]:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=nullable,
        server_default=sa.func.now() if now else None,
    )


def upgrade() -> None:
    for name, values in _ENUMS.items():
        pg.ENUM(*values, name=name).create(op.get_bind(), checkfirst=False)

    op.create_table(
        "users",
        _id(),
        sa.Column("email", pg.CITEXT(), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", _enum("user_role"), nullable=False),
        sa.Column("status", _enum("user_status"), nullable=False, server_default="pending"),
        sa.Column("is_email_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default=sa.false()),
        _tstz("last_login_at"),
        _tstz("deleted_at"),
        _tstz("created_at", nullable=False, now=True),
        _tstz("updated_at", nullable=False, now=True),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index("ix_users_created_at_id", "users", ["created_at", "id"])
    op.create_index(
        "ix_users_email_trgm",
        "users",
        ["email"],
        postgresql_using="gin",
        postgresql_ops={"email": "gin_trgm_ops"},
    )

    op.create_table(
        "profiles",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("first_name", sa.String(100), nullable=False),
        sa.Column("last_name", sa.String(100), nullable=False),
        sa.Column("phone", sa.String(32), nullable=True),
        sa.Column("avatar_key", sa.String(255), nullable=True),
        sa.Column("school", sa.String(255), nullable=True),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="UTC"),
        sa.PrimaryKeyConstraint("user_id", name="pk_profiles"),
        _fk("profiles", "user_id", "users.id"),
    )
    op.execute(
        "CREATE INDEX ix_profiles_full_name_trgm ON profiles "
        "USING gin ((first_name || ' ' || last_name) gin_trgm_ops)"
    )

    op.create_table(
        "trainer_profiles",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("business_name", sa.String(120), nullable=False),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("address", sa.String(255), nullable=True),
        sa.Column("website", sa.String(255), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("logo_key", sa.String(255), nullable=True),
        sa.Column("brand_primary_color", sa.CHAR(7), nullable=True),
        sa.Column("brand_secondary_color", sa.CHAR(7), nullable=True),
        sa.PrimaryKeyConstraint("user_id", name="pk_trainer_profiles"),
        _fk("trainer_profiles", "user_id", "users.id"),
        sa.UniqueConstraint("slug", name="uq_trainer_profiles_slug"),
        sa.CheckConstraint(
            "(brand_primary_color IS NULL OR brand_primary_color ~ '^#[0-9A-Fa-f]{6}$') AND "
            "(brand_secondary_color IS NULL OR brand_secondary_color ~ '^#[0-9A-Fa-f]{6}$')",
            name=conv("ck_trainer_profiles_brand_hex"),
        ),
    )

    op.create_table(
        "coach_profiles",
        _id(),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("trainer_id", sa.Uuid(), nullable=False),
        sa.Column("bio", sa.Text(), nullable=True),
        sa.Column("credentials", sa.Text(), nullable=True),
        sa.Column("certifications", pg.JSONB(), nullable=False, server_default="[]"),
        sa.Column("is_public", sa.Boolean(), nullable=False, server_default=sa.false()),
        _tstz("joined_at", nullable=False),
        _tstz("left_at"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.PrimaryKeyConstraint("id", name="pk_coach_profiles"),
        _fk("coach_profiles", "user_id", "users.id"),
        _fk("coach_profiles", "trainer_id", "trainer_profiles.user_id"),
        sa.CheckConstraint(
            "is_active = (left_at IS NULL)", name=conv("ck_coach_left_iff_inactive")
        ),
    )
    op.create_index(
        "uq_coach_one_active_trainer",
        "coach_profiles",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )
    op.create_index(
        "ix_coach_trainer_active",
        "coach_profiles",
        ["trainer_id"],
        postgresql_where=sa.text("is_active"),
    )

    op.create_table(
        "player_profiles",
        _id(),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("login_user_id", sa.Uuid(), nullable=True),
        sa.Column("display_name", sa.String(120), nullable=False),
        sa.Column("birth_date", sa.Date(), nullable=False),
        sa.Column("gender", sa.String(16), nullable=False),
        sa.Column("skill_level", sa.String(50), nullable=True),
        sa.Column("school", sa.String(255), nullable=True),
        sa.Column("jersey_number", sa.SmallInteger(), nullable=True),
        sa.Column("is_dependent", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("emergency_contact_name", sa.String(120), nullable=True),
        sa.Column("emergency_contact_phone", sa.String(32), nullable=True),
        sa.Column(
            "token_spend_requires_approval",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        _tstz("created_at", nullable=False, now=True),
        _tstz("updated_at", nullable=False, now=True),
        sa.PrimaryKeyConstraint("id", name="pk_player_profiles"),
        _fk("player_profiles", "owner_user_id", "users.id"),
        _fk("player_profiles", "login_user_id", "users.id"),
    )
    op.create_index("ix_player_profiles_owner_user_id", "player_profiles", ["owner_user_id"])
    op.create_index("ix_player_profiles_login_user_id", "player_profiles", ["login_user_id"])

    op.create_table(
        "share_links",
        _id(),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("trainer_id", sa.Uuid(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("kind", _enum("share_link_kind"), nullable=False),
        sa.Column("target_email", pg.CITEXT(), nullable=True),
        _tstz("expires_at"),
        sa.Column("max_uses", sa.Integer(), nullable=True),
        sa.Column("use_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        _tstz("created_at", nullable=False, now=True),
        sa.PrimaryKeyConstraint("id", name="pk_share_links"),
        _fk("share_links", "trainer_id", "trainer_profiles.user_id"),
        _fk("share_links", "created_by_user_id", "users.id"),
        sa.UniqueConstraint("code", name="uq_share_links_code"),
        sa.CheckConstraint(
            "(kind = 'coach_unique' AND target_email IS NOT NULL AND max_uses = 1 "
            "AND expires_at IS NOT NULL) OR (kind = 'player_static' AND target_email IS NULL)",
            name=conv("ck_share_links_kind_fields"),
        ),
    )
    op.create_index("ix_share_links_trainer_id", "share_links", ["trainer_id"])

    op.create_table(
        "trainer_player_links",
        _id(),
        sa.Column("trainer_id", sa.Uuid(), nullable=False),
        sa.Column("player_profile_id", sa.Uuid(), nullable=False),
        sa.Column("source", _enum("link_source"), nullable=False),
        sa.Column("share_link_id", sa.Uuid(), nullable=True),
        _tstz("linked_at", nullable=False, now=True),
        sa.Column("status", _enum("link_status"), nullable=False, server_default="active"),
        sa.PrimaryKeyConstraint("id", name="pk_trainer_player_links"),
        _fk("trainer_player_links", "trainer_id", "trainer_profiles.user_id"),
        _fk("trainer_player_links", "player_profile_id", "player_profiles.id"),
        _fk("trainer_player_links", "share_link_id", "share_links.id"),
        sa.UniqueConstraint(
            "trainer_id", "player_profile_id", name="uq_trainer_player_links_trainer_player"
        ),
    )
    op.create_index(
        "ix_trainer_player_links_trainer_status", "trainer_player_links", ["trainer_id", "status"]
    )
    op.create_index(
        "ix_trainer_player_links_player_profile_id", "trainer_player_links", ["player_profile_id"]
    )

    op.create_table(
        "share_link_events",
        _id(),
        sa.Column("share_link_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        _tstz("occurred_at", nullable=False, now=True),
        sa.Column("user_agent_hash", sa.String(64), nullable=True),
        sa.Column("ip_hash", sa.String(64), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_share_link_events"),
        _fk("share_link_events", "share_link_id", "share_links.id"),
        sa.CheckConstraint(
            "kind IN ('opened', 'registered')", name=conv("ck_share_link_events_kind")
        ),
    )
    op.create_index("ix_share_link_events_share_link_id", "share_link_events", ["share_link_id"])

    op.create_table(
        "availability_windows",
        _id(),
        sa.Column("subject_type", sa.String(16), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=False),
        sa.Column("weekday", sa.SmallInteger(), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("end_time", sa.Time(), nullable=False),
        sa.Column("is_available", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.PrimaryKeyConstraint("id", name="pk_availability_windows"),
        sa.CheckConstraint("weekday BETWEEN 0 AND 6", name=conv("ck_availability_windows_weekday")),
        sa.CheckConstraint("start_time < end_time", name=conv("ck_availability_start_before_end")),
        sa.CheckConstraint(
            "subject_type IN ('player', 'coach')", name=conv("ck_availability_windows_subject_type")
        ),
    )
    op.create_index(
        "ix_availability_windows_subject", "availability_windows", ["subject_type", "subject_id"]
    )
    op.execute(
        "ALTER TABLE availability_windows ADD CONSTRAINT ex_availability_no_overlap "
        "EXCLUDE USING gist (subject_type WITH =, subject_id WITH =, weekday WITH =, "
        "timerange(start_time, end_time, '[)') WITH &&)"
    )

    op.create_table(
        "purchase_approvals",
        _id(),
        sa.Column("player_profile_id", sa.Uuid(), nullable=False),
        sa.Column("parent_user_id", sa.Uuid(), nullable=False),
        sa.Column("trainer_id", sa.Uuid(), nullable=False),
        sa.Column("subject_type", sa.String(32), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=True),
        sa.Column("token_amount", sa.Integer(), nullable=True),
        sa.Column("status", _enum("approval_status"), nullable=False, server_default="pending"),
        _tstz("requested_at", nullable=False, now=True),
        _tstz("responded_at"),
        _tstz("expires_at", nullable=False),
        sa.Column("parent_note", sa.String(500), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_purchase_approvals"),
        _fk("purchase_approvals", "player_profile_id", "player_profiles.id"),
        _fk("purchase_approvals", "parent_user_id", "users.id"),
        _fk("purchase_approvals", "trainer_id", "trainer_profiles.user_id"),
    )
    op.create_index(
        "ix_purchase_approvals_status_expires_at", "purchase_approvals", ["status", "expires_at"]
    )
    op.create_index(
        "ix_purchase_approvals_parent_user_id", "purchase_approvals", ["parent_user_id"]
    )

    op.create_table(
        "impersonation_sessions",
        _id(),
        sa.Column("admin_user_id", sa.Uuid(), nullable=False),
        sa.Column("target_user_id", sa.Uuid(), nullable=False),
        _tstz("started_at", nullable=False, now=True),
        _tstz("ended_at"),
        _tstz("expires_at", nullable=False),
        sa.Column("reason", sa.String(500), nullable=True),
        sa.Column("jti", pg.UUID(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_impersonation_sessions"),
        _fk("impersonation_sessions", "admin_user_id", "users.id"),
        _fk("impersonation_sessions", "target_user_id", "users.id"),
        sa.UniqueConstraint("jti", name="uq_impersonation_sessions_jti"),
    )

    op.create_table(
        "user_erasures",
        _id(),
        sa.Column("original_user_id", sa.Uuid(), nullable=False),
        sa.Column("original_email_hash", sa.String(64), nullable=False),
        sa.Column("erased_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.String(500), nullable=True),
        _tstz("erased_at", nullable=False, now=True),
        _tstz("legal_retention_until", nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_user_erasures"),
        _fk("user_erasures", "erased_by_user_id", "users.id"),
    )

    op.create_table(
        "refresh_tokens",
        _id(),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("family_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.LargeBinary(), nullable=False),
        _tstz("expires_at", nullable=False),
        _tstz("consumed_at"),
        _tstz("revoked_at"),
        sa.Column("user_agent_hash", sa.String(64), nullable=True),
        sa.Column("ip_hash", sa.String(64), nullable=True),
        _tstz("created_at", nullable=False, now=True),
        sa.PrimaryKeyConstraint("id", name="pk_refresh_tokens"),
        _fk("refresh_tokens", "user_id", "users.id"),
        sa.UniqueConstraint("token_hash", name="uq_refresh_tokens_token_hash"),
    )
    op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"])
    op.create_index(
        "ix_refresh_tokens_family_live",
        "refresh_tokens",
        ["family_id"],
        postgresql_where=sa.text("revoked_at IS NULL"),
    )

    op.create_table(
        "one_time_tokens",
        _id(),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("purpose", _enum("ott_purpose"), nullable=False),
        sa.Column("token_hash", sa.LargeBinary(), nullable=False),
        sa.Column("payload", pg.JSONB(), nullable=True),
        _tstz("expires_at", nullable=False),
        _tstz("used_at"),
        _tstz("created_at", nullable=False, now=True),
        sa.PrimaryKeyConstraint("id", name="pk_one_time_tokens"),
        _fk("one_time_tokens", "user_id", "users.id"),
        sa.UniqueConstraint("token_hash", name="uq_ott_token_hash"),
    )
    op.create_index(
        "ix_ott_user_purpose_open",
        "one_time_tokens",
        ["user_id", "purpose"],
        postgresql_where=sa.text("used_at IS NULL"),
    )
    op.create_index("ix_ott_expires", "one_time_tokens", ["expires_at"])


def downgrade() -> None:
    for table in (
        "one_time_tokens",
        "refresh_tokens",
        "user_erasures",
        "impersonation_sessions",
        "purchase_approvals",
        "availability_windows",
        "share_link_events",
        "trainer_player_links",
        "share_links",
        "player_profiles",
        "coach_profiles",
        "trainer_profiles",
        "profiles",
        "users",
    ):
        op.drop_table(table)
    for name in _ENUMS:
        op.execute(f"DROP TYPE {name}")
