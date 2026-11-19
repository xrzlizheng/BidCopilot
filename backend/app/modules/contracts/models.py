from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


class ContractReview(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    """合同审查主记录。合同文件原始字节不落库：条款文本已结构化入
    contract_clauses，hash 用于同项目去重与溯源。"""

    __tablename__ = "contract_reviews"
    __table_args__ = (
        UniqueConstraint("tenant_id", "project_id", "file_hash", name="uq_contract_reviews_hash"),
        Index("ix_contract_reviews_tenant_project", "tenant_id", "project_id"),
    )

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("bid_projects.id", ondelete="CASCADE"), nullable=False
    )
    filename: Mapped[str] = mapped_column(String(300), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="reviewed")
    summary_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # 修订对照：本次审查由哪次旧审查重新上传而来（re-review 链路）。
    replaces_review_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("contract_reviews.id", ondelete="SET NULL"),
        nullable=True,
    )
    # 审查点模板匹配与应用结果（matched/selected/applied_review_points/core_purpose）。
    template_matches: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class ContractClause(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "contract_clauses"
    __table_args__ = (Index("ix_contract_clauses_review", "review_id", "seq"),)

    review_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("contract_reviews.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False, default="other")
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_span: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)



class ContractFinding(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    """审查结论 = 待确认判断：pending/confirmed/dismissed + 确认人落库。"""

    __tablename__ = "contract_findings"
    __table_args__ = (Index("ix_contract_findings_tenant_status", "tenant_id", "status"),)

    review_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("contract_reviews.id", ondelete="CASCADE"), nullable=False
    )
    clause_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("contract_clauses.id", ondelete="CASCADE"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    rule_id: Mapped[str | None] = mapped_column(String(60), nullable=True)
    severity: Mapped[str] = mapped_column(String(10), nullable=False, default="medium")
    message: Mapped[str] = mapped_column(Text, nullable=False)
    legal_basis: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_span: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    source: Mapped[str] = mapped_column(String(10), nullable=False, default="rule")
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="pending")
    # 置信度与来源引擎（rule=1.0 / heuristic=0.80 / llm=0.6，回查失败降 0.4）；
    # 同一结论被规则与 LLM 双路命中时按 6-gram 归并去重。
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(3, 2), nullable=True)
    detection_method: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # LLM 深审给出的可直接采纳改写文本；规则/租户规则不产出。
    redline: Mapped[str | None] = mapped_column(Text, nullable=True)
    confirmed_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)



class ContractReviewTemplate(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """审查点模板：识别合同类型 → 注入审查点/审查目的/审查纪律。

    is_system=True 的行是平台级只读目录（tenant_id 为空、不挂租户外键），
    随迁移种子化；租户级行 tenant_id 指向租户。两类共用一张表，
    查询必须用 or_(is_system, tenant_id==当前租户) 放行，不能套
    TenantRepository.scoped（那会把系统目录一并滤掉）。
    """

    __tablename__ = "contract_review_templates"
    __table_args__ = (
        Index(
            "ux_contract_review_templates_system",
            "contract_type",
            unique=True,
            postgresql_where=text("is_system"),
        ),
        Index(
            "ux_contract_review_templates_tenant",
            "tenant_id",
            "contract_type",
            unique=True,
            postgresql_where=text("NOT is_system"),
        ),
        Index("ix_contract_review_templates_tenant_id", "tenant_id"),
    )

    tenant_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=True,
    )
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    contract_type: Mapped[str] = mapped_column(String(60), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    contract_type_keywords: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    review_points: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    core_purposes: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    prompt_rules: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    report_sections: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    typical_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class LawArticle(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """法条知识库：深审的法条注入与 legal_basis 引用校验数据源。

    非租户表（法律是公共语料）：语料经一次性迁移/运维导入（种子为要点摘编，
    见 data/law_articles.py），运行时只读库内数据，不做实时外部检索。
    """

    __tablename__ = "law_articles"
    __table_args__ = (
        UniqueConstraint("law_name", "article_no", name="uq_law_articles_law_article"),
        Index("ix_law_articles_law_name", "law_name"),
    )

    law_name: Mapped[str] = mapped_column(String(120), nullable=False)
    article_no: Mapped[str] = mapped_column(String(20), nullable=False)
    # 阿拉伯条号：引用校验时把"第586条/第五百八十六条"统一折算比对。
    article_no_arabic: Mapped[int] = mapped_column(Integer, nullable=False)
    article_text: Mapped[str] = mapped_column(Text, nullable=False)
    effective_status: Mapped[str] = mapped_column(String(20), nullable=False, default="current")
    keywords: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    source_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class ContractTemplateVersion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """审查点模板版本快照：租户模板每次 PATCH/revert 前整体快照。

    系统模板不可变、不产生版本；template 删除随 FK CASCADE 一并清理。
    """

    __tablename__ = "contract_template_versions"
    __table_args__ = (
        UniqueConstraint(
            "template_id", "version", name="uq_contract_template_versions_template_version"
        ),
        Index("ix_contract_template_versions_template", "template_id"),
    )

    template_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("contract_review_templates.id", ondelete="CASCADE"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class ContractMilestone(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    """履约台账节点：从合同条款启发式抽取或人工补录，调度器到期提醒。"""

    __tablename__ = "contract_milestones"
    __table_args__ = (
        Index("ix_contract_milestones_due", "tenant_id", "status", "due_at"),
    )

    review_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("contract_reviews.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(16, 2), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="upcoming")
    reminded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TenantContractRule(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    """租户自定义审查规则：正则命中即 finding，优先于内置规则语义。"""

    __tablename__ = "tenant_contract_rules"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_tenant_contract_rules_name"),
    )

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    pattern: Mapped[str] = mapped_column(String(500), nullable=False)
    severity: Mapped[str] = mapped_column(String(10), nullable=False, default="medium")
    message_template: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class ContractRecord(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    """合同主数据（台账）：与既有 contract_reviews 通过 review_id 关联。

    审查能力保持独立：本表只引用 review，不改变 contract_reviews/clauses/findings
    的任何字段或生命周期。
    """

    __tablename__ = "contract_records"
    __table_args__ = (
        UniqueConstraint("tenant_id", "contract_no", name="uq_contract_records_no"),
        Index("ix_contract_records_tenant_status", "tenant_id", "status"),
        Index("ix_contract_records_tenant_project", "tenant_id", "project_id"),
        Index("ix_contract_records_expiry", "tenant_id", "status", "expiry_date"),
    )

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("bid_projects.id", ondelete="CASCADE"), nullable=False
    )
    review_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("contract_reviews.id", ondelete="SET NULL"), nullable=True
    )
    contract_no: Mapped[str] = mapped_column(String(80), nullable=False)
    title: Mapped[str] = mapped_column(String(400), nullable=False)
    party_a: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    party_b: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    signed_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    expiry_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="CNY")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    owner_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    draft_generated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    draft_disclaimer: Mapped[str | None] = mapped_column(Text, nullable=True)
    expiry_reminded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ContractDraftClause(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    """机器生成草稿的条款快照；source_json 必填，保障可追溯。"""

    __tablename__ = "contract_draft_clauses"
    __table_args__ = (Index("ix_contract_draft_clauses_record", "record_id", "seq"),)

    record_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("contract_records.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False, default="other")
    source_json: Mapped[dict] = mapped_column(JSONB, nullable=False)


class ContractApproval(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    """审批流留痕：submit/approve/reject/terminate/archive 每次动作一行。"""

    __tablename__ = "contract_approvals"
    __table_args__ = (Index("ix_contract_approvals_record", "record_id", "created_at"),)

    record_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("contract_records.id", ondelete="CASCADE"), nullable=False
    )
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    actor_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    from_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    to_status: Mapped[str | None] = mapped_column(String(20), nullable=True)


class ContractLedgerEntry(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    """履约台账：开票/收款/保证金/质保金。只追加，不覆盖历史流水。"""

    __tablename__ = "contract_ledger_entries"
    __table_args__ = (Index("ix_contract_ledger_entries_record", "record_id", "occurred_on"),)

    record_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("contract_records.id", ondelete="CASCADE"), nullable=False
    )
    entry_type: Mapped[str] = mapped_column(String(20), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    occurred_on: Mapped[date] = mapped_column(Date, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    voucher_ref: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class ContractChange(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    """变更/续签留痕：审批通过后同步主数据，但本表记录前后值永不覆盖。"""

    __tablename__ = "contract_changes"
    __table_args__ = (Index("ix_contract_changes_record", "record_id", "created_at"),)

    record_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("contract_records.id", ondelete="CASCADE"), nullable=False
    )
    change_type: Mapped[str] = mapped_column(String(20), nullable=False)
    amount_before: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    amount_after: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    expiry_before: Mapped[date | None] = mapped_column(Date, nullable=True)
    expiry_after: Mapped[date | None] = mapped_column(Date, nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    approval_status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    requested_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


