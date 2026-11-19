"""p0 opportunities and sync schema

Revision ID: 0002_p0_opportunities
Revises: 0001_p0_identity
Create Date: 2026-10-21
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0002_p0_opportunities"
down_revision: str | None = "0001_p0_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "source_sync_runs",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source", sa.String(80), nullable=False),
        sa.Column("pcode", sa.String(40), nullable=False),
        sa.Column("query_json", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("rows_fetched", sa.Integer, nullable=False, server_default="0"),
        sa.Column("rows_upserted", sa.Integer, nullable=False, server_default="0"),
        sa.Column("warnings_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("snapshot_ref", sa.Text, nullable=True),
        sa.Column("gateway_order_no", sa.String(100), nullable=True),
        sa.Column("charged", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("created_by", UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_source_sync_runs_tenant_id", "source_sync_runs", ["tenant_id"])
    op.create_index("ix_source_sync_runs_tenant_created", "source_sync_runs", ["tenant_id", "created_at"])
    op.create_table(
        "opportunities",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source", sa.String(80), nullable=False),
        sa.Column("source_id", sa.String(120), nullable=True),
        sa.Column("dedup_key", sa.String(64), nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("project_code", sa.String(200), nullable=True),
        sa.Column("project_name", sa.Text, nullable=True),
        sa.Column("buyer", sa.Text, nullable=True),
        sa.Column("agency", sa.Text, nullable=True),
        sa.Column("winner", sa.Text, nullable=True),
        sa.Column("region", sa.String(200), nullable=True),
        sa.Column("industry", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("budget_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("win_bid_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("publish_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("bid_deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("info_type", sa.String(20), nullable=False, server_default="other"),
        sa.Column("source_url", sa.Text, nullable=True),
        sa.Column("detail_status", sa.String(20), nullable=False, server_default="list_only"),
        sa.Column("confidence", sa.String(10), nullable=False, server_default="high"),
        sa.Column("content_html", sa.Text, nullable=True),
        sa.Column("content_text", sa.Text, nullable=True),
        sa.Column("content_fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_snapshot_ref", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("tenant_id", "source", "dedup_key", name="uq_opportunities_tenant_source_dedup"),
    )
    op.create_index("ix_opportunities_tenant_id", "opportunities", ["tenant_id"])
    op.create_index("ix_opportunities_tenant_publish", "opportunities", ["tenant_id", "publish_at"])
    op.create_index("ix_opportunities_tenant_info_type", "opportunities", ["tenant_id", "info_type"])
    op.create_table(
        "opportunity_snapshots",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("opportunity_id", UUID, sa.ForeignKey("opportunities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("raw_snapshot_ref", sa.Text, nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_opportunity_snapshots_tenant_id", "opportunity_snapshots", ["tenant_id"])
    op.create_index("ix_opportunity_snapshots_tenant_opp", "opportunity_snapshots", ["tenant_id", "opportunity_id"])
    op.create_table(
        "opportunity_favorites",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("opportunity_id", UUID, sa.ForeignKey("opportunities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("tenant_id", "user_id", "opportunity_id", name="uq_opportunity_favorites"),
    )
    op.create_index("ix_opportunity_favorites_tenant_id", "opportunity_favorites", ["tenant_id"])
    op.create_index("ix_opportunity_favorites_user_id", "opportunity_favorites", ["user_id"])
    op.create_table(
        "award_records",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("opportunity_id", UUID, sa.ForeignKey("opportunities.id", ondelete="SET NULL"), nullable=True),
        sa.Column("dedup_key", sa.String(64), nullable=False),
        sa.Column("project_code", sa.String(200), nullable=True),
        sa.Column("project_name", sa.Text, nullable=True),
        sa.Column("winner", sa.Text, nullable=False),
        sa.Column("win_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("award_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source", sa.String(80), nullable=False),
        sa.Column("source_url", sa.Text, nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("tenant_id", "dedup_key", name="uq_award_records_tenant_dedup"),
    )
    op.create_index("ix_award_records_tenant_id", "award_records", ["tenant_id"])


def downgrade() -> None:
    op.drop_table("award_records")
    op.drop_table("opportunity_favorites")
    op.drop_table("opportunity_snapshots")
    op.drop_table("opportunities")
    op.drop_table("source_sync_runs")
