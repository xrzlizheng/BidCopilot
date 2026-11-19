"""open api tokens

开放 API Token（面向代理机构与集成商）：只存 SHA-256 哈希，明文仅在创建响应返回一次，
token_prefix 用于列表展示与排错。

Revision ID: 0066_api_tokens
Revises: 0065_ai_usage_metering
Create Date: 2026-10-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0066_api_tokens"
down_revision: str | None = "0065_ai_usage_metering"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "api_tokens",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "tenant_id", UUID, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("name", sa.String(120), nullable=False),
        # btc_ + 随机串前 12 位：只在列表里展示，便于集成商自行对账排错。
        sa.Column("token_prefix", sa.String(24), nullable=False),
        # 只存 SHA-256 十六进制哈希，明文不落库。
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("scopes", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column(
            "created_by", UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_api_tokens_tenant_id", "api_tokens", ["tenant_id"])
    op.create_index("ix_api_tokens_tenant_created", "api_tokens", ["tenant_id", "created_at"])
    # 认证按哈希唯一命中：唯一索引同时保证不存在同值 token。
    op.create_index("ux_api_tokens_token_hash", "api_tokens", ["token_hash"], unique=True)


def downgrade() -> None:
    op.drop_index("ux_api_tokens_token_hash", table_name="api_tokens")
    op.drop_index("ix_api_tokens_tenant_created", table_name="api_tokens")
    op.drop_index("ix_api_tokens_tenant_id", table_name="api_tokens")
    op.drop_table("api_tokens")
