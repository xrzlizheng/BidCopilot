"""p1 company bid history

Revision ID: 0007_p1_company_bids
Revises: 0006_p3_compliance
Create Date: 2026-10-21
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0007_p1_company_bids"
down_revision: str | None = "0006_p3_compliance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "company_bids",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("company_id", UUID, sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("dedup_key", sa.String(64), nullable=False),
        sa.Column("source", sa.String(80), nullable=False),
        sa.Column("source_id", sa.String(120), nullable=True),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("project_name", sa.Text, nullable=True),
        sa.Column("project_code", sa.String(200), nullable=True),
        sa.Column("buyer", sa.Text, nullable=True),
        sa.Column("agency", sa.Text, nullable=True),
        sa.Column("winner", sa.Text, nullable=True),
        sa.Column("budget_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("win_bid_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("publish_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("info_type", sa.String(20), nullable=False, server_default="other"),
        sa.Column("source_url", sa.Text, nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_snapshot_ref", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("tenant_id", "dedup_key", name="uq_company_bids_tenant_dedup"),
    )
    op.create_index("ix_company_bids_tenant_id", "company_bids", ["tenant_id"])
    op.create_index("ix_company_bids_company_id", "company_bids", ["company_id"])
    op.create_index(
        "ix_company_bids_tenant_company_publish", "company_bids", ["tenant_id", "company_id", "publish_at"]
    )


def downgrade() -> None:
    op.drop_table("company_bids")
