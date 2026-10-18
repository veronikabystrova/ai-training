"""Phase 0 platform tables (mirror ``0001_baseline`` exactly)."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, Index, String, UniqueConstraint, false, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin


class IdempotencyKey(IdMixin, Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (
        UniqueConstraint("key", "principal_id", name="uq_idempotency_keys_key_principal_id"),
        Index("ix_idempotency_keys_expires_at", "expires_at"),
    )

    key: Mapped[str] = mapped_column(String(64))
    principal_id: Mapped[UUID]
    endpoint: Mapped[str] = mapped_column(String(255))
    request_hash: Mapped[str] = mapped_column(String(64))
    response_status: Mapped[int | None]
    response_body: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AuditLog(IdMixin, Base):
    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_actor_user_id", "actor_user_id"),
        Index("ix_audit_log_occurred_at", "occurred_at"),
    )

    actor_user_id: Mapped[UUID | None]
    act_user_id: Mapped[UUID | None]
    action: Mapped[str] = mapped_column(String(64))
    subject_type: Mapped[str | None] = mapped_column(String(64))
    subject_id: Mapped[UUID | None]
    trainer_id: Mapped[UUID | None]
    method: Mapped[str | None] = mapped_column(String(10))
    path: Mapped[str | None] = mapped_column(String(512))
    # `metadata` is reserved on declarative classes: map the column to `metadata_`.
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, server_default=text("'{}'::jsonb")
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class FeatureFlag(IdMixin, Base):
    __tablename__ = "feature_flags"
    __table_args__ = (
        # NULLS NOT DISTINCT: a second global flag (trainer_id NULL) must collide (PG >= 15).
        UniqueConstraint(
            "key",
            "trainer_id",
            name="uq_feature_flags_key_trainer_id",
            postgresql_nulls_not_distinct=True,
        ),
    )

    key: Mapped[str] = mapped_column(String(64))
    trainer_id: Mapped[UUID | None]
    enabled: Mapped[bool] = mapped_column(server_default=false())
