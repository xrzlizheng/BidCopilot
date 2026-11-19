import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ContractClauseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    seq: int
    title: str
    text: str
    category: str
    page: int | None
    source_span: dict[str, Any]


class ContractFindingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    clause_id: UUID | None
    kind: str
    rule_id: str | None
    severity: str
    message: str
    legal_basis: str | None
    evidence_span: dict[str, Any]
    source: str
    status: str
    confirmed_by: UUID | None
    confirmed_at: datetime | None
    # 置信度（rule=1.0 / heuristic=0.7-0.9 / llm=0.6，证据回查失败降 0.4）与来源引擎。
    confidence: Decimal | None
    detection_method: str | None
    redline: str | None


class ContractReviewTemplateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    is_system: bool
    contract_type: str
    name: str
    contract_type_keywords: list[str]
    review_points: list[str]
    core_purposes: list[str]
    prompt_rules: list[str]
    report_sections: list[str]
    typical_description: str | None
    enabled: bool


def _trim_list(items: list[str], *, cap: int, item_max: int) -> list[str]:
    cleaned: list[str] = []
    for item in items:
        value = item.strip()[:item_max]
        if value:
            cleaned.append(value)
        if len(cleaned) >= cap:
            break
    return cleaned


class ContractTemplateUpsert(BaseModel):
    """租户审查点模板创建/更新体；文本逐字进 prompt，全部限长。"""

    contract_type: str
    name: str
    contract_type_keywords: list[str] = Field(default_factory=list)
    review_points: list[str] = Field(default_factory=list)
    core_purposes: list[str] = Field(default_factory=list)
    prompt_rules: list[str] = Field(default_factory=list)
    report_sections: list[str] = Field(default_factory=list)
    typical_description: str | None = None
    enabled: bool = True

    @field_validator(
        "contract_type_keywords",
        "review_points",
        "core_purposes",
        "prompt_rules",
        "report_sections",
    )
    @classmethod
    def _trim_lists(cls, value: list[str], info: Any) -> list[str]:
        caps = {
            "contract_type_keywords": (20, 60),
            "review_points": (60, 500),
            "core_purposes": (20, 300),
            "prompt_rules": (30, 500),
            "report_sections": (20, 60),
        }
        cap, item_max = caps[info.field_name]
        return _trim_list(value, cap=cap, item_max=item_max)

    @field_validator("contract_type")
    @classmethod
    def _contract_type_slug(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if not re.fullmatch(r"[a-z0-9_-]{1,60}", cleaned):
            raise ValueError("contract_type 只允许小写字母/数字/下划线/连字符，长度 1-60")
        return cleaned

    @field_validator("name")
    @classmethod
    def _name_required(cls, value: str) -> str:
        cleaned = value.strip()[:200]
        if not cleaned:
            raise ValueError("模板名称不能为空")
        return cleaned

    @field_validator("review_points")
    @classmethod
    def _points_required(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("审查点不能为空（模板的最低价值就是审查点）")
        return value

    @field_validator("typical_description")
    @classmethod
    def _desc_cap(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip()[:1000] or None


class ContractTemplateVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    template_id: UUID
    version: int
    snapshot: dict[str, Any]
    created_by: UUID | None
    created_at: datetime


class ContractReviewResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    filename: str
    size_bytes: int
    file_hash: str
    status: str
    summary_json: dict[str, Any]
    created_by: UUID | None
    created_at: datetime
    replaces_review_id: UUID | None
    template_matches: dict[str, Any] | None


class ContractReviewDetailResponse(ContractReviewResponse):
    clauses: list[ContractClauseResponse]
    findings: list[ContractFindingResponse]
    disclaimer: str


class FindingStatusUpdate(BaseModel):
    status: str

    model_config = ConfigDict(json_schema_extra={"example": {"status": "confirmed"}})



# ---------------------------------------------------------------------------
# CLM 精简版：合同主数据 / 起草 / 审批 / 履约台账 / 变更续签
# ---------------------------------------------------------------------------


def _validate_decimal_amount(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, float):
        raise ValueError("金额请使用字符串或整数，禁止浮点")
    try:
        amount = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("金额格式非法") from exc
    if amount < 0:
        raise ValueError("金额不能为负数")
    return amount.quantize(Decimal("0.01"))


class ContractRecordCreate(BaseModel):
    project_id: UUID
    contract_no: str
    title: str
    party_a: str = ""
    party_b: str = ""
    signed_date: date | None = None
    effective_date: date | None = None
    expiry_date: date | None = None
    amount: Decimal | None = None
    currency: str = "CNY"
    review_id: UUID | None = None
    owner_id: UUID | None = None

    @field_validator("contract_no", "title")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("不能为空")
        return value.strip()

    @field_validator("amount", mode="before")
    @classmethod
    def _amount_decimal(cls, value: Any) -> Decimal | None:
        return _validate_decimal_amount(value)

    @model_validator(mode="after")
    def _date_order(self) -> "ContractRecordCreate":
        if self.effective_date and self.expiry_date and self.expiry_date < self.effective_date:
            raise ValueError("到期日不能早于生效日")
        if self.signed_date and self.expiry_date and self.expiry_date < self.signed_date:
            raise ValueError("到期日不能早于签约日")
        return self


class ContractRecordUpdate(BaseModel):
    title: str | None = None
    party_a: str | None = None
    party_b: str | None = None
    signed_date: date | None = None
    effective_date: date | None = None
    expiry_date: date | None = None
    amount: Decimal | None = None
    currency: str | None = None
    review_id: UUID | None = None
    owner_id: UUID | None = None

    @field_validator("title")
    @classmethod
    def _title_not_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("合同名称不能为空")
        return value.strip() if value is not None else None

    @field_validator("amount", mode="before")
    @classmethod
    def _amount_decimal(cls, value: Any) -> Decimal | None:
        return _validate_decimal_amount(value)


class ContractDraftClauseResponse(BaseModel):
    id: UUID
    seq: int
    title: str
    text: str
    category: str
    source_json: dict[str, Any]


class ContractRecordResponse(BaseModel):
    id: UUID
    project_id: UUID
    review_id: UUID | None
    contract_no: str
    title: str
    party_a: str
    party_b: str
    signed_date: date | None
    effective_date: date | None
    expiry_date: date | None
    amount: str | None
    currency: str
    status: str
    owner_id: UUID | None
    created_by: UUID | None
    draft_generated_at: datetime | None
    draft_disclaimer: str | None
    expiry_reminded_at: datetime | None
    created_at: datetime


class ContractRecordDetailResponse(ContractRecordResponse):
    draft: list[ContractDraftClauseResponse] = []


class ContractDraftResponse(BaseModel):
    record: ContractRecordDetailResponse
    disclaimer: str
    generated_count: int


class ContractActionRequest(BaseModel):
    comment: str | None = None


class ContractApprovalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    record_id: UUID
    action: str
    actor_id: UUID | None
    comment: str | None
    from_status: str | None
    to_status: str | None
    created_at: datetime


class LedgerEntryCreate(BaseModel):
    entry_type: Literal["invoice", "payment", "deposit", "retention"]
    amount: Decimal
    occurred_on: date
    note: str | None = None
    voucher_ref: str | None = None

    @field_validator("amount", mode="before")
    @classmethod
    def _amount_decimal(cls, value: Any) -> Decimal:
        validated = _validate_decimal_amount(value)
        if validated is None:
            raise ValueError("金额不能为空")
        return validated


class LedgerEntryResponse(BaseModel):
    id: UUID
    record_id: UUID
    entry_type: str
    amount: str
    occurred_on: date
    note: str | None
    voucher_ref: str | None
    created_by: UUID | None
    created_at: datetime


class LedgerSummaryResponse(BaseModel):
    contract_amount: str
    invoice_total: str
    payment_total: str
    deposit_total: str
    retention_total: str
    outstanding: str


class LedgerListResponse(BaseModel):
    record_id: UUID
    currency: str
    summary: LedgerSummaryResponse
    entries: list[LedgerEntryResponse]


class ContractChangeCreate(BaseModel):
    change_type: Literal["change", "renewal"]
    amount_before: Decimal | None = None
    amount_after: Decimal | None = None
    expiry_before: date | None = None
    expiry_after: date | None = None
    reason: str

    @field_validator("amount_before", "amount_after", mode="before")
    @classmethod
    def _amount_decimal(cls, value: Any) -> Decimal | None:
        return _validate_decimal_amount(value)

    @field_validator("reason")
    @classmethod
    def _reason_required(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("变更原因不能为空")
        return value.strip()


class ContractChangeResponse(BaseModel):
    id: UUID
    record_id: UUID
    change_type: str
    amount_before: str | None
    amount_after: str | None
    expiry_before: date | None
    expiry_after: date | None
    reason: str
    approval_status: str
    requested_by: UUID | None
    approved_by: UUID | None
    approved_at: datetime | None
    created_at: datetime



class ContractRecordListResponse(BaseModel):
    items: list[ContractRecordResponse]
    total: int
    limit: int
    offset: int


class ContractChangeListResponse(BaseModel):
    items: list[ContractChangeResponse]
