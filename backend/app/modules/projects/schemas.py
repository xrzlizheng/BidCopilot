from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ProjectCreate(BaseModel):
    opportunity_id: UUID | None = None
    company_id: UUID | None = None
    name: str | None = Field(default=None, max_length=400)
    code: str | None = None
    bid_deadline: datetime | None = None


class ProjectStatusChange(BaseModel):
    """项目状态流转。

    只允许通过这个接口改状态，不让 ProjectUpdate 直接写 status——
    状态是所有下游判断（在投提醒、就绪校验、影响评估）的基础，
    随便改会让这些判断失去依据。
    """

    status: Literal["active", "won", "lost"]
    reason: str | None = Field(default=None, max_length=500)


class ProjectUpdate(BaseModel):
    company_id: UUID | None = None
    name: str | None = Field(default=None, max_length=400)
    code: str | None = None
    bid_deadline: datetime | None = None
    internal_deadline: datetime | None = None
    package_code: str | None = Field(default=None, max_length=100)


class ProjectResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    opportunity_id: UUID | None
    company_id: UUID | None
    name: str
    code: str | None
    buyer: str | None
    status: str
    bid_deadline: datetime | None
    internal_deadline: datetime | None = None
    package_code: str | None = None
    decision_status: str | None
    decision_json: dict[str, Any]
    template_code: str | None = None
    format_code: str | None = None
    created_at: datetime


class DocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    filename: str
    content_type: str | None
    size_bytes: int
    file_hash: str
    object_ref: str
    parse_status: str
    parse_error: str | None
    parsed_at: datetime | None
    ocr_used: bool = False
    document_kind: str = "tender"
    assembly_meta: dict[str, Any] = Field(default_factory=dict)


class DocumentDiffApply(BaseModel):
    confirm: bool = False
    note: str | None = Field(default=None, max_length=1000)


ReadinessStatus = Literal["pending", "done", "blocked", "not_applicable", "waived"]


class ReadinessTemplateInfo(BaseModel):
    code: str
    name: str
    version: int
    description: str = ""
    item_count: int | None = None


class ReadinessItemResponse(BaseModel):
    id: UUID
    item_key: str
    template_code: str
    template_version: int
    category: str
    label: str
    source_type: str
    rule_id: str | None = None
    is_blocking: bool
    applicability: str
    evidence_required: bool
    status: ReadinessStatus
    owner_id: UUID | None = None
    collaborator_ids: list[UUID] = Field(default_factory=list)
    due_at: datetime | None = None
    completed_at: datetime | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    note: str | None = None
    waived_reason: str | None = None
    confirmed_by: UUID | None = None
    confirmed_at: datetime | None = None
    source_state: str
    synced_at: datetime | None = None
    stage: str
    sort_order: int


class ReadinessSummary(BaseModel):
    total: int
    done: int
    blocked: int
    pending: int
    not_applicable: int
    waived: int
    blocking_open: int
    completion_rate: float


class ReadinessResponse(BaseModel):
    template: ReadinessTemplateInfo
    bid_deadline: datetime | None = None
    internal_deadline: datetime | None = None
    items: list[ReadinessItemResponse]
    summary: ReadinessSummary


class DecisionCardInputs(BaseModel):
    quote_amount: Decimal | None = None
    estimated_cost: Decimal | None = None
    tax_rate: Decimal | None = None
    competition_intensity: str | None = Field(default=None, max_length=100)


class DecisionCardRequest(BaseModel):
    inputs: DecisionCardInputs = Field(default_factory=DecisionCardInputs)


class DecisionDimension(BaseModel):
    key: str
    label: str
    score: float | None = None
    weight: float
    scored: bool
    reason: str
    source: str


class DecisionVeto(BaseModel):
    code: str
    label: str
    reason: str


class DecisionCardResponse(BaseModel):
    id: UUID
    project_id: UUID
    run_no: int
    engine_mode: str
    engine_version: str
    constraint_version: int | None = None
    score: float | None = None
    computed_status: str
    status: str
    human_status: str | None = None
    dimensions: list[DecisionDimension]
    vetoes: list[DecisionVeto]
    thresholds: dict[str, Any]
    source_labels: dict[str, Any]
    inputs: dict[str, Any] | None = None
    # 判定当时的快照（含企业划型）。企业规模会变，回头看「当初为何判 No-Go」
    # 全靠它，否则结论无法审计。
    profile_snapshot: dict[str, Any] | None = None
    created_at: datetime
    confirmed_at: datetime | None = None
    override_reason: str | None = None


class DecisionCardConfirm(BaseModel):
    human_status: Literal["Go", "Conditional Go", "No-Go"]
    override_reason: str | None = Field(default=None, max_length=2000)


class ReadinessUpdate(BaseModel):
    status: ReadinessStatus | None = None
    note: str | None = Field(default=None, max_length=2000)
    owner_id: UUID | None = None
    collaborator_ids: list[UUID] | None = None
    due_at: datetime | None = None
    evidence_refs: list[str] | None = None
    waived_reason: str | None = Field(default=None, max_length=1000)


class ConstraintResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    version: int
    payload: dict[str, Any]
    parse_meta: dict[str, Any]
    created_at: datetime


class ScoringItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    category: str
    item: str
    score: Decimal | None
    criteria: str | None
    source_span: dict[str, Any]
    confidence: str


class MatrixResponse(BaseModel):
    scoring_item_id: UUID
    item: str
    category: str
    score: Decimal | None
    coverage_status: str
    evidence_required: list[Any]
    matched_assets: list[Any]
    missing_evidence: list[Any]
    response_strategy: str
    risk_note: str | None
    section_mapping: str | None


class QualificationItemResponse(BaseModel):
    requirement_index: int
    category: str
    text: str
    level: str | None
    quantity: int | None
    status: str
    reason: str
    matched_asset_ids: list[str]
    source_span: dict[str, Any] | None = None


class DecisionResponse(BaseModel):
    status: str
    summary: str
    factors: list[dict[str, str]]


BidOutcome = Literal["won", "lost", "cancelled"]
LossReason = Literal["price", "technical", "qualification", "compliance", "relationship", "budget", "other"]


class BidResultUpsert(BaseModel):
    outcome: BidOutcome
    win_amount: Decimal | None = Field(default=None, ge=0)
    loss_reason: LossReason | None = None
    competitor: str | None = Field(default=None, max_length=300)
    notes: str | None = Field(default=None, max_length=4000)


class BidResultResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    outcome: str
    win_amount: Decimal | None
    loss_reason: str | None
    competitor: str | None
    notes: str | None
    recorded_by: UUID | None
    recorded_at: datetime


ResponseStatus = Literal["pending", "responded", "deviated", "not_responded"]


class ComplianceResponseItem(BaseModel):
    id: UUID
    requirement_type: str
    requirement_text: str
    category: str
    response_status: ResponseStatus
    response_note: str | None = None
    evidence_refs: list[Any] = Field(default_factory=list)
    section_ref: str | None = None
    source_span: dict[str, Any] = Field(default_factory=dict)
    confirmed_by: UUID | None = None
    confirmed_at: datetime | None = None


class ComplianceResponseList(BaseModel):
    items: list[ComplianceResponseItem]
    summary: dict[str, int]
    total: int
    unresolved: int
    constraint_version: int | None = None


class ComplianceResponseUpdate(BaseModel):
    response_status: ResponseStatus | None = None
    response_note: str | None = Field(default=None, max_length=2000)
    evidence_refs: list[str] | None = None
    section_ref: str | None = Field(default=None, max_length=100)


class ComplianceResponseBatch(BaseModel):
    response_ids: list[UUID] = Field(min_length=1, max_length=500)
    response_status: ResponseStatus
    response_note: str | None = Field(default=None, max_length=2000)


class ComplianceResponseBatchResult(BaseModel):
    updated: int
    summary: dict[str, int]
    unresolved: int


class CommitmentSnapshotRequest(BaseModel):
    quote_amount: Decimal | None = None
    duration_days: int | None = None
    warranty_days: int | None = None
    personnel: list[str] | None = None


class BidCommitmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    kind: str
    label: str
    value_text: str
    value_amount: Decimal | None
    value_days: int | None
    source: str
    confirmed_by: UUID | None
    confirmed_at: datetime | None


class SubmissionReceiptCreate(BaseModel):
    submitted_at: datetime
    platform: str = Field(min_length=1, max_length=100)
    receipt_no: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=2000)


class SubmissionReceiptAttachmentResponse(BaseModel):
    id: UUID
    receipt_id: UUID
    kind: str
    filename: str
    content_type: str
    size_bytes: int
    sha256: str
    uploaded_by: UUID | None
    created_at: datetime


class SubmissionReceiptResponse(BaseModel):
    id: UUID
    project_id: UUID
    submitted_at: datetime
    platform: str
    receipt_no: str | None
    notes: str | None
    operator_id: UUID | None
    checklist_summary: dict[str, Any]
    created_at: datetime
    attachments: list[SubmissionReceiptAttachmentResponse] = Field(default_factory=list)
