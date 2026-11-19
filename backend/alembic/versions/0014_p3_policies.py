"""p3 compliance policies

Revision ID: 0014_p3_policies
Revises: 0013_p1_templates
Create Date: 2026-10-21
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0014_p3_policies"
down_revision: str | None = "0013_p1_templates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "policies",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("rule_id", sa.String(80), nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("severity_override", sa.String(20), nullable=True),
        sa.Column("updated_by", UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("tenant_id", "rule_id", name="uq_policies_tenant_rule"),
    )
    op.create_index("ix_policies_tenant_id", "policies", ["tenant_id"])


def downgrade() -> None:
    op.drop_table("policies")
