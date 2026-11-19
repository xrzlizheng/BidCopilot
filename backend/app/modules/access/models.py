from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


class ResourceAcl(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "resource_acl"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "resource_type",
            "resource_id",
            "subject_type",
            "subject_id",
            name="uq_resource_acl_subject",
        ),
        Index(
            "ix_resource_acl_tenant_subject",
            "tenant_id",
            "subject_type",
            "subject_id",
            "resource_type",
        ),
        Index("ix_resource_acl_tenant_resource", "tenant_id", "resource_type", "resource_id"),
    )

    resource_type: Mapped[str] = mapped_column(String(60), nullable=False)
    resource_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(20), nullable=False)
    subject_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    include_descendants: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    granted_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
