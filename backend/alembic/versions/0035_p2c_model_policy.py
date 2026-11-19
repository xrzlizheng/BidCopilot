"""p2c model policy

Revision ID: 0035_p2c_model_policy
Revises: 0034_p2c_department_assets
Create Date: 2026-10-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0035_p2c_model_policy"
down_revision: str | None = "0034_p2c_department_assets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "tenant_settings",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "tenant_id",
            UUID,
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("key", sa.String(120), nullable=False),
        sa.Column("value", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("updated_by", UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "key", name="uq_tenant_settings_tenant_key"),
    )
    op.create_index("ix_tenant_settings_tenant_id", "tenant_settings", ["tenant_id"])


def downgrade() -> None:
    # 共享环境禁止 destructive downgrade；向前修复。
    pass
