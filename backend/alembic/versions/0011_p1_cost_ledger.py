"""p1 data cost ledger

Revision ID: 0011_p1_cost_ledger
Revises: 0010_p3_bid_evaluations
Create Date: 2026-10-21
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0011_p1_cost_ledger"
down_revision: str | None = "0010_p3_bid_evaluations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "cost_ledger",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("pcode", sa.String(60), nullable=False),
        sa.Column("operation", sa.String(60), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("order_no", sa.String(100), nullable=True),
        sa.Column("charged", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("rows", sa.Integer, nullable=False, server_default="0"),
        sa.Column("query_hash", sa.String(64), nullable=True),
        sa.Column("resource_type", sa.String(40), nullable=True),
        sa.Column("resource_id", sa.String(100), nullable=True),
        sa.Column("created_by", UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_cost_ledger_tenant_id", "cost_ledger", ["tenant_id"])
    op.create_index("ix_cost_ledger_tenant_created", "cost_ledger", ["tenant_id", "created_at"])
    op.create_index("ix_cost_ledger_tenant_pcode", "cost_ledger", ["tenant_id", "pcode"])


def downgrade() -> None:
    op.drop_table("cost_ledger")
