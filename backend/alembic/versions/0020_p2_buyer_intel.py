"""p2 opportunity buyer intel cache

Revision ID: 0020_p2_buyer_intel
Revises: 0019_p2_subs_notifications
Create Date: 2026-10-23
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0020_p2_buyer_intel"
down_revision: str | None = "0019_p2_subs_notifications"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "opportunity_buyer_intel",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("opportunity_id", UUID, sa.ForeignKey("opportunities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("buyer_name", sa.String(300), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("winner_rows", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("top_winners", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("existing_suppliers", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("recent_wins_12m", sa.Integer, nullable=False, server_default="0"),
        sa.Column("rows_fetched", sa.Integer, nullable=False, server_default="0"),
        sa.Column("snapshot_ref", sa.Text, nullable=True),
        sa.Column("gateway_order_no", sa.String(100), nullable=True),
        sa.Column("charged", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("created_by", UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("tenant_id", "opportunity_id", name="uq_opportunity_buyer_intel"),
    )
    op.create_index("ix_opportunity_buyer_intel_tenant_id", "opportunity_buyer_intel", ["tenant_id"])
    op.create_index(
        "ix_opportunity_buyer_intel_tenant_opportunity",
        "opportunity_buyer_intel",
        ["tenant_id", "opportunity_id"],
    )


def downgrade() -> None:
    op.drop_table("opportunity_buyer_intel")
