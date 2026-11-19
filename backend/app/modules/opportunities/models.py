from datetime import datetime
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


class SourceSyncRun(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "source_sync_runs"
    __table_args__ = (Index("ix_source_sync_runs_tenant_created", "tenant_id", "created_at"),)

    source: Mapped[str] = mapped_column(String(80), nullable=False)
    pcode: Mapped[str] = mapped_column(String(40), nullable=False)
    query_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    source_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    rows_fetched: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_upserted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_new: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    warnings_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    warnings: Mapped[list[dict]] = mapped_column(
        "warnings_json", JSONB, nullable=False, default=list
    )
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    snapshot_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    gateway_order_no: Mapped[str | None] = mapped_column(String(100), nullable=True)
    charged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Opportunity(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "opportunities"
    __table_args__ = (
        UniqueConstraint("tenant_id", "source", "dedup_key", name="uq_opportunities_tenant_source_dedup"),
        Index("ix_opportunities_tenant_publish", "tenant_id", "publish_at"),
        Index("ix_opportunities_tenant_info_type", "tenant_id", "info_type"),
    )

    source: Mapped[str] = mapped_column(String(80), nullable=False)
    source_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    dedup_key: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    project_code: Mapped[str | None] = mapped_column(String(200), nullable=True)
    project_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    buyer: Mapped[str | None] = mapped_column(Text, nullable=True)
    agency: Mapped[str | None] = mapped_column(Text, nullable=True)
    winner: Mapped[str | None] = mapped_column(Text, nullable=True)
    region: Mapped[str | None] = mapped_column(String(200), nullable=True)
    industry: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    budget_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    win_bid_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    publish_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    bid_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    info_type: Mapped[str] = mapped_column(String(20), nullable=False, default="other")
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    detail_status: Mapped[str] = mapped_column(String(20), nullable=False, default="list_only")
    confidence: Mapped[str] = mapped_column(String(10), nullable=False, default="high")
    content_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    raw_snapshot_ref: Mapped[str | None] = mapped_column(Text, nullable=True)


class OpportunitySnapshot(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "opportunity_snapshots"
    __table_args__ = (Index("ix_opportunity_snapshots_tenant_opp", "tenant_id", "opportunity_id"),)

    opportunity_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("opportunities.id", ondelete="CASCADE"), nullable=False
    )
    raw_snapshot_ref: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OpportunityFavorite(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "opportunity_favorites"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", "opportunity_id", name="uq_opportunity_favorites"),
    )

    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    opportunity_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("opportunities.id", ondelete="CASCADE"), nullable=False
    )


class OpportunitySubscription(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "opportunity_subscriptions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", "name", name="uq_opportunity_subscriptions_name"),
        Index("ix_opportunity_subscriptions_tenant_enabled", "tenant_id", "enabled"),
    )

    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    query_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    interval_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sync_run_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("source_sync_runs.id", ondelete="SET NULL"), nullable=True
    )


class OpportunityBuyerIntel(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "opportunity_buyer_intel"
    __table_args__ = (
        UniqueConstraint("tenant_id", "opportunity_id", name="uq_opportunity_buyer_intel"),
    )

    opportunity_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("opportunities.id", ondelete="CASCADE"), nullable=False
    )
    buyer_name: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    winner_rows: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list)
    top_winners: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list)
    existing_suppliers: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list)
    recent_wins_12m: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_fetched: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    snapshot_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    gateway_order_no: Mapped[str | None] = mapped_column(String(100), nullable=True)
    charged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class AwardRecord(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "award_records"
    __table_args__ = (UniqueConstraint("tenant_id", "dedup_key", name="uq_award_records_tenant_dedup"),)

    opportunity_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("opportunities.id", ondelete="SET NULL"), nullable=True
    )
    dedup_key: Mapped[str] = mapped_column(String(64), nullable=False)
    project_code: Mapped[str | None] = mapped_column(String(200), nullable=True)
    project_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    winner: Mapped[str] = mapped_column(Text, nullable=False)
    win_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    award_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source: Mapped[str] = mapped_column(String(80), nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BuyerWatchlist(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "opportunity_buyer_watchlist"
    __table_args__ = (
        UniqueConstraint("tenant_id", "buyer_name", name="uq_opportunity_buyer_watchlist"),
        Index("ix_opportunity_buyer_watchlist_tenant", "tenant_id", "created_at"),
    )

    buyer_name: Mapped[str] = mapped_column(String(300), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class OpportunityMatch(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    """机会匹配结果（启发式规则打分，持久化，避免只在列表页临时计算）。"""

    __tablename__ = "opportunity_matches"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "opportunity_id", name="uq_opportunity_matches_tenant_opportunity"
        ),
        Index("ix_opportunity_matches_tenant_decision", "tenant_id", "decision"),
        Index("ix_opportunity_matches_tenant_computed", "tenant_id", "computed_at"),
    )

    opportunity_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("opportunities.id", ondelete="CASCADE"), nullable=False
    )
    match_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    hit_groups_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    reasons_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    risks_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    # None 表示资格要求未能解析或无法判定，不能伪造成 False。
    qualified: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    qualification_gaps_json: Mapped[list[dict]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    decision: Mapped[str] = mapped_column(String(20), nullable=False, default="review")
    engine_version: Mapped[str] = mapped_column(String(40), nullable=False, default="heuristic-rules-v1")
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    project_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("bid_projects.id", ondelete="SET NULL"), nullable=True
    )
