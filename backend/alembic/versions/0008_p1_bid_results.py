"""p1 bid result retrospective

Revision ID: 0008_p1_bid_results
Revises: 0007_p1_company_bids
Create Date: 2026-10-21
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0008_p1_bid_results"
down_revision: str | None = "0007_p1_company_bids"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "bid_results",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", UUID, sa.ForeignKey("bid_projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("outcome", sa.String(20), nullable=False),
        sa.Column("win_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("loss_reason", sa.String(40), nullable=True),
        sa.Column("competitor", sa.Text, nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("recorded_by", UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("project_id", name="uq_bid_results_project"),
    )
    op.create_index("ix_bid_results_tenant_id", "bid_results", ["tenant_id"])
    op.create_index("ix_bid_results_tenant_recorded", "bid_results", ["tenant_id", "recorded_at"])


def downgrade() -> None:
    op.drop_table("bid_results")
