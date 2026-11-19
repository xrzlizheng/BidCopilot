from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


class ModelConfig(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "model_configs"
    __table_args__ = (Index("ix_model_configs_tenant_scope", "tenant_id", "scope"),)

    purpose: Mapped[str] = mapped_column(String(20), nullable=False)
    scope: Mapped[str] = mapped_column(String(20), nullable=False, default="tenant")
    owner_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    provider: Mapped[str] = mapped_column(String(40), nullable=False, default="openai_compatible")
    base_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    secret_ref: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("secret_store.id", ondelete="SET NULL"), nullable=True
    )
    key_last4: Mapped[str | None] = mapped_column(String(4), nullable=True)
    updated_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class ModelSecret(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "secret_store"
    __table_args__ = (
        Index("ix_secret_store_tenant_scope", "tenant_id", "scope"),
        Index("ix_secret_store_tenant_owner", "tenant_id", "owner_id"),
    )

    scope: Mapped[str] = mapped_column(String(20), nullable=False)
    owner_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    provider: Mapped[str] = mapped_column(String(40), nullable=False, default="openai_compatible")
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    wrapped_dek: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    dek_nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    last4: Mapped[str] = mapped_column(String(4), nullable=False)
    created_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TenantSetting(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "tenant_settings"
    __table_args__ = (
        UniqueConstraint("tenant_id", "key", name="uq_tenant_settings_tenant_key"),
    )

    key: Mapped[str] = mapped_column(String(120), nullable=False)
    value: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    updated_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
