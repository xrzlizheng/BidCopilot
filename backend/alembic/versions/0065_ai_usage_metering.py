"""ai usage metering and plan ai entitlements

按项目/按篇计量：记录每次 AI 生成（篇数）与产出字数，落到项目与客户维度，供代理机构
按客户分摊成本；用量上限校验由部署形态决定，本迁移只负责计量表。

Revision ID: 0065_ai_usage_metering
Revises: 0064_receipt_attachments
Create Date: 2026-10-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0065_ai_usage_metering"
down_revision: str | None = "0064_receipt_attachments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:

    op.create_table(
        "ai_usage_ledger",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "tenant_id", UUID, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "project_id",
            UUID,
            sa.ForeignKey("bid_projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "client_id", UUID, sa.ForeignKey("clients.id", ondelete="SET NULL"), nullable=True
        ),
        # generation / evaluation / rewrite ...
        sa.Column("operation", sa.String(60), nullable=False),
        # ok / failed / partial —— 原样记录，不把失败当成 0 消耗
        sa.Column("status", sa.String(20), nullable=False, server_default="ok"),
        sa.Column("piece_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("word_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("model", sa.String(120), nullable=True),
        sa.Column(
            "created_by", UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "ix_ai_usage_tenant_project", "ai_usage_ledger", ["tenant_id", "project_id"]
    )
    op.create_index("ix_ai_usage_tenant_client", "ai_usage_ledger", ["tenant_id", "client_id"])
    op.create_index("ix_ai_usage_tenant_created", "ai_usage_ledger", ["tenant_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_ai_usage_tenant_created", table_name="ai_usage_ledger")
    op.drop_index("ix_ai_usage_tenant_client", table_name="ai_usage_ledger")
    op.drop_index("ix_ai_usage_tenant_project", table_name="ai_usage_ledger")
    op.drop_table("ai_usage_ledger")
