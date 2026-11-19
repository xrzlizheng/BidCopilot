from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin

DEFAULT_WATCH_SECTIONS = "enforced,dishonest,admin_penalty,abnormal_operation"


class IntelWatchlist(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "intel_watchlist"
    __table_args__ = (
        UniqueConstraint("tenant_id", "ent_name", name="uq_intel_watchlist_tenant_ent"),
        Index("ix_intel_watchlist_tenant_active", "tenant_id", "active"),
    )

    ent_name: Mapped[str] = mapped_column(String(300), nullable=False)
    label: Mapped[str | None] = mapped_column(String(300), nullable=True)
    sections: Mapped[str] = mapped_column(
        String(400), nullable=False, default=DEFAULT_WATCH_SECTIONS
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    last_digest: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
