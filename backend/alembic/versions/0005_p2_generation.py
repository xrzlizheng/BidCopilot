"""p2 generation and evidence schema

Revision ID: 0005_p2_generation
Revises: 0004_p1_projects
Create Date: 2026-10-21
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0005_p2_generation"
down_revision: str | None = "0004_p1_projects"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "generation_runs",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", UUID, sa.ForeignKey("bid_projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="running"),
        sa.Column("sections_total", sa.Integer, nullable=False, server_default="0"),
        sa.Column("sections_generated", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("created_by", UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_generation_runs_tenant_id", "generation_runs", ["tenant_id"])
    op.create_index("ix_generation_runs_tenant_project", "generation_runs", ["tenant_id", "project_id"])
    op.create_table(
        "bid_sections",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", UUID, sa.ForeignKey("bid_projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("run_id", UUID, sa.ForeignKey("generation_runs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("scoring_item_id", UUID, sa.ForeignKey("scoring_items.id", ondelete="SET NULL"), nullable=True),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("section_type", sa.String(40), nullable=False),
        sa.Column("order_index", sa.Integer, nullable=False),
        sa.Column("content_json", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("content_text", sa.Text, nullable=False, server_default=""),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("generation_mode", sa.String(20), nullable=True),
        sa.Column("factcheck_status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("traceability_rate", sa.Numeric(5, 4), nullable=True),
        sa.Column("unsourced_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by", UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("project_id", "order_index", name="uq_bid_sections_project_order"),
    )
    op.create_index("ix_bid_sections_tenant_id", "bid_sections", ["tenant_id"])
    op.create_index("ix_bid_sections_tenant_project", "bid_sections", ["tenant_id", "project_id"])
    op.create_table(
        "evidence_links",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", UUID, sa.ForeignKey("bid_projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("section_id", UUID, sa.ForeignKey("bid_sections.id", ondelete="CASCADE"), nullable=False),
        sa.Column("claim", sa.Text, nullable=False),
        sa.Column("claim_type", sa.String(40), nullable=False, server_default="other"),
        sa.Column("status", sa.String(20), nullable=False, server_default="unsourced"),
        sa.Column("source_type", sa.String(40), nullable=True),
        sa.Column("source_ref", sa.String(200), nullable=True),
        sa.Column("source_quote", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_evidence_links_tenant_id", "evidence_links", ["tenant_id"])
    op.create_index("ix_evidence_links_tenant_section", "evidence_links", ["tenant_id", "section_id"])


def downgrade() -> None:
    op.drop_table("evidence_links")
    op.drop_table("bid_sections")
    op.drop_table("generation_runs")
