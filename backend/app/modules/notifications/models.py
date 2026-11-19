from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


class Notification(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notifications_tenant_user_created", "tenant_id", "user_id", "created_at"),
        Index(
            "ix_notifications_tenant_user_unread",
            "tenant_id",
            "user_id",
            postgresql_where=text("read_at IS NULL"),
        ),
    )

    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    type: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    resource_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class NotificationChannel(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "notification_channels"
    __table_args__ = (
        UniqueConstraint("tenant_id", "kind", "target", name="uq_notification_channels"),
    )

    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    target: Mapped[str] = mapped_column(String(500), nullable=False)
    label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
