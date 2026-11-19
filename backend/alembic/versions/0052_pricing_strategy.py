"""price strategy snapshots

Revision ID: 0052_pricing_strategy
Revises: 0051_company_enterprise_scale
Create Date: 2026-11-01

报价策略每次生成都保留输入与结果快照，便于复核「当时为什么建议这个价」。历史
样本不足时 result_json.status 为 insufficient_data，不会写入猜测值。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID

from alembic import op

revision: str = "0052_pricing_strategy"
down_revision: str | None = "0051_company_enterprise_scale"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "price_strategies",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("bid_projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("inputs_json", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("result_json", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "engine_version",
            sa.String(40),
            nullable=False,
            server_default="pricing-v1",
        ),
        sa.Column(
            "created_by",
            PGUUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_price_strategies_tenant_project",
        "price_strategies",
        ["tenant_id", "project_id", "created_at"],
    )


def downgrade() -> None:
    # 共享环境禁止 destructive downgrade；向前修复。
    pass
