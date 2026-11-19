from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class OpportunitySyncRequest(BaseModel):
    keywords: list[str] = Field(default_factory=list)
    info_types: list[str] = Field(default_factory=lambda: ["1", "2", "3"])
    publish_from: date | None = None
    publish_to: date | None = None
    region_list: list[dict[str, str]] | None = None
    industry_list: list[str] | None = None
    budget_min: Decimal | None = None
    budget_max: Decimal | None = None
    search_key: dict | None = None
    page_size: int = Field(default=50, ge=1, le=50)


class AwardResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    winner: str
    win_amount: Decimal | None
    award_at: datetime | None
    source_url: str | None


class OpportunityExplanation(BaseModel):
    reasons: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    buyer_watched: bool = False
    buyer_intel: dict | None = None


class OpportunityMatchSummary(BaseModel):
    match_score: int
    hit_groups: list[str] = Field(default_factory=list)
    qualified: bool | None = None
    decision: str = "review"
    computed_at: datetime


class OpportunityListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    project_code: str | None
    project_name: str | None
    buyer: str | None
    agency: str | None
    winner: str | None
    region: str | None
    industry: list[str]
    budget_amount: Decimal | None
    win_bid_amount: Decimal | None
    publish_at: datetime | None
    bid_deadline: datetime | None
    info_type: str
    source: str
    source_url: str | None
    detail_status: str
    fetched_at: datetime
    match_hits: int | None = None
    explanation: OpportunityExplanation | None = None
    match: OpportunityMatchSummary | None = None


class BuyerWatchlistCreate(BaseModel):
    buyer_name: str = Field(min_length=1, max_length=300)
    note: str | None = Field(default=None, max_length=1000)


class BuyerWatchlistResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    buyer_name: str
    note: str | None
    created_by: UUID | None
    created_at: datetime


class CoverageItem(BaseModel):
    subscription_id: UUID
    name: str
    keywords: list[str] = Field(default_factory=list)
    matched: int
    zero_hit: bool


class CoverageResponse(BaseModel):
    total_opportunities: int
    active_subscriptions: int
    items: list[CoverageItem]


class OpportunityDetail(OpportunityListItem):
    content_html: str | None
    content_text: str | None
    content_fetched_at: datetime | None
    awards: list[AwardResponse] = Field(default_factory=list)
    is_favorite: bool = False
    detail_error: dict[str, str] | None = None


class SyncWarningResponse(BaseModel):
    code: str
    message: str
    field: str | None = None


class SyncRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    status: str
    source_status: str | None
    rows_fetched: int
    rows_upserted: int
    rows_new: int
    warnings_count: int
    warnings: list[SyncWarningResponse] = Field(default_factory=list)
    error_code: str | None
    error_message: str | None
    snapshot_ref: str | None
    gateway_order_no: str | None
    charged: bool
    created_at: datetime
    finished_at: datetime | None


class FavoriteResponse(BaseModel):
    favorited: bool


class BuyerWinnerResponse(BaseModel):
    winner: str
    wins: int
    recent_wins_12m: int = 0
    latest_at: str | None = None
    total_amount: str | None = None
    tracked: bool = False
    competitor_noted: bool = False


class OpportunityBuyerIntelResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    opportunity_id: UUID
    buyer_name: str
    status: str
    rows_fetched: int
    recent_wins_12m: int
    top_winners: list[BuyerWinnerResponse]
    existing_suppliers: list[BuyerWinnerResponse]
    winner_rows: list[dict[str, Any]]
    synced_at: datetime | None
    snapshot_ref: str | None
    gateway_order_no: str | None
    charged: bool
    error_code: str | None
    error_message: str | None
    created_at: datetime


class OpportunitySubscriptionCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    interval_minutes: int | None = Field(default=None, ge=1, le=10080)
    query: OpportunitySyncRequest = Field(default_factory=OpportunitySyncRequest)


class OpportunitySubscriptionUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    interval_minutes: int | None = Field(default=None, ge=1, le=10080)
    enabled: bool | None = None
    query: OpportunitySyncRequest | None = None


class OpportunitySubscriptionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    query_json: dict
    interval_minutes: int
    enabled: bool
    last_run_at: datetime | None
    last_sync_run_id: UUID | None
    created_at: datetime


class QualificationGapResponse(BaseModel):
    requirement: str
    category: str
    status: str
    reason: str
    level: str | None = None
    quantity: int | None = None


class OpportunityMatchResponse(BaseModel):
    id: UUID
    opportunity_id: UUID
    match_score: int
    hit_groups: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    qualified: bool | None = None
    qualification_gaps: list[QualificationGapResponse] = Field(default_factory=list)
    decision: str
    engine_version: str
    computed_at: datetime
    project_id: UUID | None = None


class AutoMatchSettingsResponse(BaseModel):
    enabled: bool
    recommend_min_score: int
    skip_below_score: int
    auto_create_project: bool
    notify: bool
    source: str = "default"


class AutoMatchSettingsUpdate(BaseModel):
    enabled: bool | None = None
    recommend_min_score: int | None = Field(default=None, ge=0, le=100)
    skip_below_score: int | None = Field(default=None, ge=0, le=100)
    auto_create_project: bool | None = None
    notify: bool | None = None


class AutoMatchRunRequest(BaseModel):
    limit: int = Field(default=100, ge=1, le=500)
    force: bool = False


class AutoMatchRunResponse(BaseModel):
    enabled: bool
    scanned: int
    matched: int
    recommend: int
    review: int
    skip: int
    projects_created: int
    notifications_sent: int
    errors: int
