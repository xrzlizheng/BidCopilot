from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class GenerateRequest(BaseModel):
    mode: str = Field(default="rules", pattern="^(rules|llm)$")
    section_ids: list[UUID] | None = None


class SectionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    section_type: str
    order_index: int
    scoring_item_id: UUID | None
    content_json: list[Any]
    content_text: str
    ai_meta: dict[str, Any] = Field(default_factory=dict)
    status: str
    generation_mode: str | None
    factcheck_status: str
    traceability_rate: Decimal | None
    unsourced_count: int
    approved_at: datetime | None


class SectionUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=300)
    content_text: str | None = None
    content_json: list[dict[str, Any]] | None = None


class ApproveRequest(BaseModel):
    acknowledge_unsourced: bool = False


class EvidenceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    claim: str
    claim_type: str
    status: str
    source_type: str | None
    source_ref: str | None
    source_quote: str | None


class GenerateResponse(BaseModel):
    run_id: UUID
    status: str
    sections_total: int
    sections_generated: int
    error_code: str | None
    error_message: str | None


class ExportRequest(BaseModel):
    dark_bid: bool = False
    acknowledge_unresolved: bool = False
