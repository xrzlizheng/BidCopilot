from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


class Company(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "companies"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_companies_tenant_name"),)

    name: Mapped[str] = mapped_column(String(300), nullable=False)
    credit_code: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    region: Mapped[str | None] = mapped_column(String(200), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(120), nullable=True)
    contacts: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    source: Mapped[str] = mapped_column(String(40), nullable=False, default="manual")
    source_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 中小企业划型所需原始指标（单位：人 / 万元）。
    # 「专门面向中小企业」的项目填错声明函即废标，所以这些字段必须有据可查，
    # 不能只存一个「小型」的结论。
    employee_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    annual_revenue: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    total_assets: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    enterprise_industry: Mapped[str | None] = mapped_column(String(40), nullable=True)
    enterprise_scale: Mapped[str | None] = mapped_column(String(10), nullable=True)
    # auto=系统按标准计算；manual=人工确认覆盖（人工值优先，且不自动重算）
    enterprise_scale_source: Mapped[str] = mapped_column(
        String(20), nullable=False, default="auto", server_default="auto"
    )
    enterprise_scale_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Qualification(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "company_qualifications"
    __table_args__ = (
        UniqueConstraint("tenant_id", "dedup_key", name="uq_qualifications_tenant_dedup"),
        Index("ix_qualifications_tenant_org", "tenant_id", "org_unit_id"),
    )

    company_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    org_unit_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("org_units.id", ondelete="SET NULL"), nullable=True
    )
    dedup_key: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    level: Mapped[str | None] = mapped_column(String(80), nullable=True)
    cert_no: Mapped[str | None] = mapped_column(String(120), nullable=True)
    issuer: Mapped[str | None] = mapped_column(String(200), nullable=True)
    valid_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    source: Mapped[str] = mapped_column(String(40), nullable=False, default="manual")


class Personnel(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "company_personnel"
    __table_args__ = (
        UniqueConstraint("tenant_id", "dedup_key", name="uq_personnel_tenant_dedup"),
        Index("ix_personnel_tenant_org", "tenant_id", "org_unit_id"),
    )

    company_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    org_unit_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("org_units.id", ondelete="SET NULL"), nullable=True
    )
    dedup_key: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    cert_type: Mapped[str] = mapped_column(String(120), nullable=False)
    level: Mapped[str | None] = mapped_column(String(80), nullable=True)
    major: Mapped[str | None] = mapped_column(String(120), nullable=True)
    cert_no: Mapped[str | None] = mapped_column(String(120), nullable=True)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    source: Mapped[str] = mapped_column(String(40), nullable=False, default="manual")


class Performance(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "company_performances"
    __table_args__ = (
        UniqueConstraint("tenant_id", "dedup_key", name="uq_performances_tenant_dedup"),
        Index("ix_performances_tenant_org", "tenant_id", "org_unit_id"),
    )

    company_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    org_unit_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("org_units.id", ondelete="SET NULL"), nullable=True
    )
    dedup_key: Mapped[str] = mapped_column(String(64), nullable=False)
    project_name: Mapped[str] = mapped_column(String(300), nullable=False)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    complete_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    buyer: Mapped[str | None] = mapped_column(String(300), nullable=True)
    category: Mapped[str | None] = mapped_column(String(120), nullable=True)
    source: Mapped[str] = mapped_column(String(40), nullable=False, default="manual")


class Credit(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "company_credits"
    __table_args__ = (
        UniqueConstraint("tenant_id", "dedup_key", name="uq_credits_tenant_dedup"),
        Index("ix_credits_tenant_org", "tenant_id", "org_unit_id"),
    )

    company_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    org_unit_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("org_units.id", ondelete="SET NULL"), nullable=True
    )
    dedup_key: Mapped[str] = mapped_column(String(64), nullable=False)
    check_item: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(80), nullable=False)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source: Mapped[str] = mapped_column(String(40), nullable=False, default="manual")


class CompanyBid(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "company_bids"
    __table_args__ = (
        UniqueConstraint("tenant_id", "dedup_key", name="uq_company_bids_tenant_dedup"),
    )

    company_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    dedup_key: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(80), nullable=False)
    source_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    project_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    project_code: Mapped[str | None] = mapped_column(String(200), nullable=True)
    buyer: Mapped[str | None] = mapped_column(Text, nullable=True)
    agency: Mapped[str | None] = mapped_column(Text, nullable=True)
    winner: Mapped[str | None] = mapped_column(Text, nullable=True)
    budget_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    win_bid_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    publish_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    info_type: Mapped[str] = mapped_column(String(20), nullable=False, default="other")
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    raw_snapshot_ref: Mapped[str | None] = mapped_column(Text, nullable=True)


class FieldPolicy(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "field_policies"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "resource_type", "field_name", name="uq_field_policies_field"
        ),
    )

    resource_type: Mapped[str] = mapped_column(String(60), nullable=False)
    field_name: Mapped[str] = mapped_column(String(120), nullable=False)
    min_clearance: Mapped[str] = mapped_column(String(20), nullable=False, default="confidential")
    mask_strategy: Mapped[str] = mapped_column(String(20), nullable=False, default="mask")
    updated_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
