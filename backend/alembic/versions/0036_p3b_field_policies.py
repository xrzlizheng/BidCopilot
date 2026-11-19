"""p3b field policies

Revision ID: 0036_p3b_field_policies
Revises: 0035_p2c_model_policy
Create Date: 2026-10-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0036_p3b_field_policies"
down_revision: str | None = "0035_p2c_model_policy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "field_policies",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "tenant_id",
            UUID,
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("resource_type", sa.String(60), nullable=False),
        sa.Column("field_name", sa.String(120), nullable=False),
        sa.Column("min_clearance", sa.String(20), nullable=False, server_default="confidential"),
        sa.Column("mask_strategy", sa.String(20), nullable=False, server_default="mask"),
        sa.Column("updated_by", UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "tenant_id", "resource_type", "field_name", name="uq_field_policies_field"
        ),
    )
    op.create_index("ix_field_policies_tenant_id", "field_policies", ["tenant_id"])


def downgrade() -> None:
    # 共享环境禁止 destructive downgrade；向前修复。
    pass
