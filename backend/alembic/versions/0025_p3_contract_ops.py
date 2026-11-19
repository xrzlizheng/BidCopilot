"""p3 contract ops (stage 5.3: milestones / tenant custom rules)

Revision ID: 0025_p3_contract_ops
Revises: 0024_p3_contracts
Create Date: 2026-10-24
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0025_p3_contract_ops"
down_revision: str | None = "0024_p3_contracts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB


def upgrade() -> None:
    op.create_table(
        "contract_milestones",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("review_id", UUID, sa.ForeignKey("contract_reviews.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),  # payment/delivery/acceptance/warranty
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("amount", sa.Numeric(16, 2), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="upcoming"),
        sa.Column("reminded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index(
        "ix_contract_milestones_due",
        "contract_milestones",
        ["tenant_id", "status", "due_at"],
    )

    op.create_table(
        "tenant_contract_rules",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("pattern", sa.String(500), nullable=False),  # 正则；命中即 finding
        sa.Column("severity", sa.String(10), nullable=False, server_default="medium"),
        sa.Column("message_template", sa.Text, nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("created_by", UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("tenant_id", "name", name="uq_tenant_contract_rules_name"),
    )


def downgrade() -> None:
    op.drop_table("tenant_contract_rules")
    op.drop_index("ix_contract_milestones_due", table_name="contract_milestones")
    op.drop_table("contract_milestones")
