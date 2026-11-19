from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class FindingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    rule_id: str
    category: str
    severity: str
    finding_type: str
    message: str
    location: dict[str, Any]
    expected: str | None
    actual: str | None
    basis: str | None
    suggestion: str | None
    auto_fixable: bool
    needs_review: bool
    status: str
    evidence_ref: str | None
    created_at: datetime


class ReportResponse(BaseModel):
    id: UUID
    project_id: UUID
    status: str
    rule_version: str
    file_hash: str
    summary_json: dict[str, Any]
    created_at: datetime
    findings: list[FindingResponse]


class ReportSummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    status: str
    rule_version: str
    file_hash: str
    summary_json: dict[str, Any]
    created_at: datetime


class FindingUpdate(BaseModel):
    status: str = Field(pattern="^(open|fixed|accepted)$")


class ReportExportRequest(BaseModel):
    format: Literal["xlsx", "pdf"] = "xlsx"


class PolicyResponse(BaseModel):
    rule_id: str
    name: str
    category: str
    type: str
    default_severity: str
    effective_severity: str
    effective_threshold: float | None = None
    enabled: bool
    overridden: bool


class PolicyUpdate(BaseModel):
    enabled: bool = True
    severity_override: Literal["high", "medium", "low"] | None = None
    threshold_override: float | None = Field(default=None, ge=0.0, le=1.0)


class AccessRequestCreate(BaseModel):
    resource_type: Literal["knowledge_base", "knowledge_pack"]
    resource_id: UUID
    reason: str = Field(min_length=5, max_length=2000)
    requested_minutes: int = Field(default=30, ge=1, le=1440)


class AccessRequestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    resource_type: str
    resource_id: UUID
    requester_id: UUID
    approver_id: UUID | None
    reason: str
    status: str
    requested_minutes: int
    expires_at: datetime | None
    decided_at: datetime | None
    created_at: datetime


class PersonalResourceMetadata(BaseModel):
    resource_type: str
    id: UUID
    name: str
    owner_id: UUID | None
    owner_email: str | None
    classification: str
    status: str
    owner_status: str
    created_at: datetime
    document_count: int = 0
    size_bytes: int = 0
    version: str | None = None


class PersonalResourceTransfer(BaseModel):
    target_user_id: UUID
