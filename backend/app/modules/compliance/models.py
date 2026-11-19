from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


class ComplianceReport(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "compliance_reports"
    __table_args__ = (Index("ix_compliance_reports_tenant_project", "tenant_id", "project_id"),)

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("bid_projects.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="completed")
    rule_version: Mapped[str] = mapped_column(String(40), nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    summary_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class Finding(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "findings"
    __table_args__ = (Index("ix_findings_tenant_report", "tenant_id", "report_id"),)

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("bid_projects.id", ondelete="CASCADE"), nullable=False
    )
    report_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("compliance_reports.id", ondelete="CASCADE"),
        nullable=False,
    )
    rule_id: Mapped[str] = mapped_column(String(80), nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    finding_type: Mapped[str] = mapped_column(String(20), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    location: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    expected: Mapped[str | None] = mapped_column(Text, nullable=True)
    actual: Mapped[str | None] = mapped_column(Text, nullable=True)
    basis: Mapped[str | None] = mapped_column(Text, nullable=True)
    suggestion: Mapped[str | None] = mapped_column(Text, nullable=True)
    auto_fixable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    needs_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")
    evidence_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)


class CompliancePolicy(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "policies"
    __table_args__ = (UniqueConstraint("tenant_id", "rule_id", name="uq_policies_tenant_rule"),)

    rule_id: Mapped[str] = mapped_column(String(80), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    severity_override: Mapped[str | None] = mapped_column(String(20), nullable=True)
    threshold_override: Mapped[float | None] = mapped_column(Float, nullable=True)
    updated_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class ComplianceAccessRequest(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "compliance_access_requests"
    __table_args__ = (
        Index(
            "ix_compliance_access_requests_tenant_status",
            "tenant_id",
            "status",
        ),
        Index(
            "ix_compliance_access_requests_tenant_requester",
            "tenant_id",
            "requester_id",
        ),
    )

    resource_type: Mapped[str] = mapped_column(String(40), nullable=False)
    resource_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    requester_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    approver_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    requested_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
