from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


class GenerationRun(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "generation_runs"
    __table_args__ = (Index("ix_generation_runs_tenant_project", "tenant_id", "project_id"),)

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("bid_projects.id", ondelete="CASCADE"), nullable=False
    )
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")
    sections_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sections_generated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BidSection(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "bid_sections"
    __table_args__ = (
        UniqueConstraint("project_id", "order_index", name="uq_bid_sections_project_order"),
        Index("ix_bid_sections_tenant_project", "tenant_id", "project_id"),
    )

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("bid_projects.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("generation_runs.id", ondelete="SET NULL"), nullable=True
    )
    scoring_item_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("scoring_items.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    section_type: Mapped[str] = mapped_column(String(40), nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content_json: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    content_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    ai_meta: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    generation_mode: Mapped[str | None] = mapped_column(String(20), nullable=True)
    factcheck_status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    traceability_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    unsourced_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class EvidenceLink(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "evidence_links"
    __table_args__ = (Index("ix_evidence_links_tenant_section", "tenant_id", "section_id"),)

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("bid_projects.id", ondelete="CASCADE"), nullable=False
    )
    section_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("bid_sections.id", ondelete="CASCADE"), nullable=False
    )
    claim: Mapped[str] = mapped_column(Text, nullable=False)
    claim_type: Mapped[str] = mapped_column(String(40), nullable=False, default="other")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="unsourced")
    source_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    source_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)
    source_quote: Mapped[str | None] = mapped_column(Text, nullable=True)
