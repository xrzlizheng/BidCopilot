from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CompanyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    credit_code: str | None = None
    region: str | None = None
    industry: str | None = None
    contacts: dict = Field(default_factory=dict)
    # 中小企业划型原始指标（人 / 万元）。缺项则系统不给结论，避免误判影响投标。
    employee_count: int | None = Field(default=None, ge=0, le=1_000_000)
    annual_revenue: Decimal | None = Field(default=None, ge=0)
    total_assets: Decimal | None = Field(default=None, ge=0)
    enterprise_industry: str | None = None
    enterprise_scale: Literal["大型", "中型", "小型", "微型"] | None = None


class CompanyUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=300)
    credit_code: str | None = None
    region: str | None = None
    industry: str | None = None
    contacts: dict | None = None
    employee_count: int | None = Field(default=None, ge=0, le=1_000_000)
    annual_revenue: Decimal | None = Field(default=None, ge=0)
    total_assets: Decimal | None = Field(default=None, ge=0)
    enterprise_industry: str | None = None
    enterprise_scale: Literal["大型", "中型", "小型", "微型"] | None = None


class CompanyScaleResponse(BaseModel):
    """划型结果：包含依据与缺失项，便于用户核对后确认。"""

    scale: str | None
    industry: str | None
    industry_label: str | None
    basis: str
    missing: list[str]
    is_sme: bool | None
    source: str
    confirmed: bool


class CompanyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    credit_code: str | None
    region: str | None
    industry: str | None
    contacts: dict
    source: str
    employee_count: int | None = None
    annual_revenue: Decimal | None = None
    total_assets: Decimal | None = None
    enterprise_industry: str | None = None
    enterprise_scale: str | None = None
    enterprise_scale_source: str = "auto"


class QualificationCreate(BaseModel):
    company_id: UUID
    org_unit_id: UUID | None = None
    name: str
    level: str | None = None
    cert_no: str | None = None
    issuer: str | None = None
    valid_from: date | None = None
    valid_to: date | None = None


class PersonnelCreate(BaseModel):
    company_id: UUID
    org_unit_id: UUID | None = None
    name: str
    cert_type: str
    level: str | None = None
    major: str | None = None
    cert_no: str | None = None
    valid_to: date | None = None


class PerformanceCreate(BaseModel):
    company_id: UUID
    org_unit_id: UUID | None = None
    project_name: str
    amount: Decimal | None = None
    complete_date: date | None = None
    buyer: str | None = None
    category: str | None = None


class CreditCreate(BaseModel):
    company_id: UUID
    org_unit_id: UUID | None = None
    check_item: str
    status: str
    checked_at: datetime | None = None


class AssetItem(BaseModel):
    id: UUID
    company_id: UUID
    org_unit_id: UUID | None = None
    kind: str
    # 字段策略可能把 name 整列隐藏，展示层允许为 null
    name: str | None = None
    level: str | None = None
    cert_no: str | None = None
    issuer: str | None = None
    valid_from: date | None = None
    valid_to: date | None = None
    cert_type: str | None = None
    major: str | None = None
    project_name: str | None = None
    amount: Decimal | None = None
    complete_date: date | None = None
    buyer: str | None = None
    category: str | None = None
    check_item: str | None = None
    status: str | None = None
    checked_at: datetime | None = None
    source: str


class ImportErrorItem(BaseModel):
    row: int
    field: str | None = None
    message: str

class ImportResult(BaseModel):
    imported: int
    failed: int
    errors: list[ImportErrorItem]


class ExpiringItem(BaseModel):
    id: UUID
    company_id: UUID
    kind: str
    name: str | None = None
    valid_to: date


class FieldPolicyUpsert(BaseModel):
    resource_type: Literal["qualifications", "personnel", "performances", "credits"]
    field_name: str = Field(min_length=1, max_length=120)
    min_clearance: Literal["public", "internal", "confidential", "restricted"] = "confidential"
    mask_strategy: Literal["hide", "mask", "partial"] = "mask"


class FieldPolicyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    resource_type: str
    field_name: str
    min_clearance: str
    mask_strategy: str


CompanyBidRole = Literal["winner", "tenderer", "agency"]


class BidSyncRequest(BaseModel):
    roles: list[CompanyBidRole] = Field(default=["winner"], min_length=1, max_length=3)


class CompanyBidResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    company_id: UUID
    role: str
    title: str
    project_name: str | None
    project_code: str | None
    buyer: str | None
    agency: str | None
    winner: str | None
    budget_amount: Decimal | None
    win_bid_amount: Decimal | None
    publish_at: datetime | None
    info_type: str
    source_url: str | None
    source: str
    fetched_at: datetime
