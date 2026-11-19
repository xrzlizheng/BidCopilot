from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    Boolean,
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


class BidProject(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "bid_projects"
    __table_args__ = (
        Index("ix_bid_projects_tenant_status", "tenant_id", "status"),
        Index("ix_bid_projects_tenant_created", "tenant_id", "created_at"),
    )

    opportunity_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("opportunities.id", ondelete="SET NULL"), nullable=True
    )
    company_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("companies.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(400), nullable=False)
    code: Mapped[str | None] = mapped_column(String(200), nullable=True)
    buyer: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    owner_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    bid_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    package_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    decision_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    decision_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    decision_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    template_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    format_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    readiness_template_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    readiness_template_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    internal_deadline: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class TenderDocument(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "tender_documents"
    __table_args__ = (Index("ix_tender_documents_tenant_project", "tenant_id", "project_id"),)

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("bid_projects.id", ondelete="CASCADE"), nullable=False
    )
    filename: Mapped[str] = mapped_column(String(300), nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    object_ref: Mapped[str] = mapped_column(Text, nullable=False)
    parse_status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    parse_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    parsed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ocr_used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    supersedes_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tender_documents.id", ondelete="SET NULL"), nullable=True
    )
    document_kind: Mapped[str] = mapped_column(
        String(20), nullable=False, default="tender", server_default="tender"
    )
    assembly_meta: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class TenderConstraint(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "tender_constraints"
    __table_args__ = (
        UniqueConstraint("project_id", "version", name="uq_tender_constraints_project_version"),
    )

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("bid_projects.id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tender_documents.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    parse_meta: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class ScoringItem(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "scoring_items"
    __table_args__ = (Index("ix_scoring_items_tenant_project", "tenant_id", "project_id"),)

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("bid_projects.id", ondelete="CASCADE"), nullable=False
    )
    constraint_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tender_constraints.id", ondelete="CASCADE"), nullable=False
    )
    category: Mapped[str] = mapped_column(String(40), nullable=False, default="其他")
    item: Mapped[str] = mapped_column(Text, nullable=False)
    score: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    criteria: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_span: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    confidence: Mapped[str] = mapped_column(String(10), nullable=False, default="medium")


class ResponseMatrix(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "response_matrix"
    __table_args__ = (
        UniqueConstraint("scoring_item_id", name="uq_response_matrix_scoring_item"),
        Index("ix_response_matrix_tenant_project", "tenant_id", "project_id"),
    )

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("bid_projects.id", ondelete="CASCADE"), nullable=False
    )
    scoring_item_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("scoring_items.id", ondelete="CASCADE"), nullable=False
    )
    response_strategy: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_required: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    matched_assets: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    missing_evidence: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    section_mapping: Mapped[str | None] = mapped_column(String(100), nullable=True)
    coverage_status: Mapped[str] = mapped_column(String(20), nullable=False, default="missing")
    risk_note: Mapped[str | None] = mapped_column(Text, nullable=True)


class BidResult(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "bid_results"
    __table_args__ = (UniqueConstraint("project_id", name="uq_bid_results_project"),)

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("bid_projects.id", ondelete="CASCADE"), nullable=False
    )
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    win_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    loss_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    competitor: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    recorded_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class ComplianceResponse(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    """逐条响应/偏离表：对招标文件的每一项实质性要求给出响应状态与依据。"""

    __tablename__ = "compliance_responses"
    __table_args__ = (
        UniqueConstraint("constraint_id", "requirement_key", name="uq_compliance_responses_key"),
        Index("ix_compliance_responses_tenant_project", "tenant_id", "project_id"),
    )

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("bid_projects.id", ondelete="CASCADE"), nullable=False
    )
    constraint_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tender_constraints.id", ondelete="CASCADE"), nullable=False
    )
    requirement_type: Mapped[str] = mapped_column(String(30), nullable=False)
    requirement_key: Mapped[str] = mapped_column(String(64), nullable=False)
    requirement_text: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False, default="其他")
    source_span: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    response_status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    response_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_refs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    section_ref: Mapped[str | None] = mapped_column(String(100), nullable=True)
    confirmed_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ProjectReadinessItem(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    """交标就绪清单：auto 项投影 findings/responses，manual 项人工确认。"""

    __tablename__ = "project_readiness_items"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "template_version",
            "item_key",
            name="uq_project_readiness_item",
        ),
        Index("ix_project_readiness_tenant_project", "tenant_id", "project_id", "status"),
        Index("ix_project_readiness_tenant_owner", "tenant_id", "owner_id", "due_at"),
    )

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("bid_projects.id", ondelete="CASCADE"), nullable=False
    )
    template_code: Mapped[str] = mapped_column(String(100), nullable=False)
    template_version: Mapped[int] = mapped_column(Integer, nullable=False)
    item_key: Mapped[str] = mapped_column(String(100), nullable=False)
    category: Mapped[str] = mapped_column(String(60), nullable=False, default="其他")
    label: Mapped[str] = mapped_column(String(300), nullable=False)
    source_type: Mapped[str] = mapped_column(String(20), nullable=False, default="manual")
    rule_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    requirement_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    finding_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    is_blocking: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    applicability: Mapped[str] = mapped_column(String(20), nullable=False, default="required")
    evidence_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    owner_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    collaborator_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    evidence_refs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    waived_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    waived_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    waived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_state: Mapped[str] = mapped_column(String(20), nullable=False, default="stale")
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stage: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class BidDecisionRun(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "bid_decision_runs"
    __table_args__ = (
        UniqueConstraint("project_id", "run_no", name="uq_bid_decision_run_no"),
        Index("ix_bid_decision_runs_tenant_project", "tenant_id", "project_id", "created_at"),
    )

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("bid_projects.id", ondelete="CASCADE"), nullable=False
    )
    run_no: Mapped[int] = mapped_column(Integer, nullable=False)
    engine_mode: Mapped[str] = mapped_column(String(20), nullable=False, default="full")
    engine_version: Mapped[str] = mapped_column(String(40), nullable=False, default="decision-v1")
    constraint_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tender_constraints.id", ondelete="SET NULL"), nullable=True
    )
    constraint_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    company_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("companies.id", ondelete="SET NULL"), nullable=True
    )
    profile_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    inputs_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    dimensions_json: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    vetoes_json: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    score: Mapped[float | None] = mapped_column(Numeric(6, 2), nullable=True)
    computed_status: Mapped[str] = mapped_column(String(30), nullable=False)
    human_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    thresholds_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    source_labels: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    confirmed_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    override_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class ProjectReminder(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "project_reminders"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "node", "offset_days", name="uq_project_reminder_node_offset"
        ),
        Index("ix_project_reminders_tenant_status", "tenant_id", "status", "scheduled_at"),
    )

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("bid_projects.id", ondelete="CASCADE"), nullable=False
    )
    node: Mapped[str] = mapped_column(String(40), nullable=False)
    offset_days: Mapped[int] = mapped_column(Integer, nullable=False)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    dedup_key: Mapped[str] = mapped_column(String(200), nullable=False)


class ParseJob(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    """长文档异步解析任务（7.3）：状态机 queued/running/succeeded/failed。"""

    __tablename__ = "parse_jobs"
    __table_args__ = (Index("ix_parse_jobs_tenant_status", "tenant_id", "status"),)

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("bid_projects.id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tender_documents.id", ondelete="CASCADE"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class BidCommitment(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    """投标承诺快照：报价/工期/质保/人员；供合同履约衔接对照。"""

    __tablename__ = "bid_commitments"
    __table_args__ = (
        UniqueConstraint("project_id", "kind", "label", name="uq_bid_commitment"),
        Index("ix_bid_commitments_tenant_project", "tenant_id", "project_id"),
    )

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("bid_projects.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    value_text: Mapped[str] = mapped_column(Text, nullable=False)
    value_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    value_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(String(40), nullable=False, default="manual")
    confirmed_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SubmissionReceipt(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "submission_receipts"
    __table_args__ = (
        Index("ix_submission_receipts_tenant_project", "tenant_id", "project_id"),
    )

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("bid_projects.id", ondelete="CASCADE"), nullable=False
    )
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    platform: Mapped[str] = mapped_column(String(100), nullable=False)
    receipt_no: Mapped[str | None] = mapped_column(String(200), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    operator_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    checklist_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
