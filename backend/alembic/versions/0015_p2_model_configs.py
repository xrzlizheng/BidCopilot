"""p2 tenant model configs

Revision ID: 0015_p2_model_configs
Revises: 0014_p3_policies
Create Date: 2026-10-21
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0015_p2_model_configs"
down_revision: str | None = "0014_p3_policies"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "model_configs",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("purpose", sa.String(20), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False, server_default="openai_compatible"),
        sa.Column("base_url", sa.Text, nullable=True),
        sa.Column("model", sa.String(120), nullable=True),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("updated_by", UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("tenant_id", "purpose", name="uq_model_configs_tenant_purpose"),
    )
    op.create_index("ix_model_configs_tenant_id", "model_configs", ["tenant_id"])


def downgrade() -> None:
    op.drop_table("model_configs")
