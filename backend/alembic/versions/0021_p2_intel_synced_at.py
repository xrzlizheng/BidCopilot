"""p2 buyer intel last-success timestamp and redundant index cleanup

Revision ID: 0021_p2_intel_synced_at
Revises: 0020_p2_buyer_intel
Create Date: 2026-10-24
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0021_p2_intel_synced_at"
down_revision: str | None = "0020_p2_buyer_intel"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "opportunity_buyer_intel",
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
    )
    # 唯一约束 (tenant_id, opportunity_id) 已覆盖该前缀，0020 的复合索引冗余。
    op.drop_index(
        "ix_opportunity_buyer_intel_tenant_opportunity",
        table_name="opportunity_buyer_intel",
    )
    # 0020 曾把上游状态二值化为 succeeded/failed；现在改为 DataResult.status 原样保存。
    op.execute("UPDATE opportunity_buyer_intel SET status = 'ok' WHERE status = 'succeeded'")
    op.execute(
        "UPDATE opportunity_buyer_intel SET synced_at = created_at "
        "WHERE synced_at IS NULL AND status = 'ok'"
    )


def downgrade() -> None:
    op.create_index(
        "ix_opportunity_buyer_intel_tenant_opportunity",
        "opportunity_buyer_intel",
        ["tenant_id", "opportunity_id"],
    )
    op.drop_column("opportunity_buyer_intel", "synced_at")
