"""webhook endpoints and deliveries

Webhook 出站：endpoint 的 secret 不落明文，写库时复用 secret_store 的 AES-GCM 加密能力
（webhook_endpoints.secret_ref -> secret_store.id），投递记录用于排错与审计。

Revision ID: 0067_webhooks
Revises: 0066_api_tokens
Create Date: 2026-10-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0067_webhooks"
down_revision: str | None = "0066_api_tokens"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "webhook_endpoints",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "tenant_id", UUID, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("url", sa.String(1000), nullable=False),
        # 明文不落库：指向 secret_store 的加密记录（AES-GCM + 主密钥包裹 DEK）。
        sa.Column(
            "secret_ref",
            UUID,
            sa.ForeignKey("secret_store.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("events", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_by", UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("tenant_id", "url", name="uq_webhook_endpoints_tenant_url"),
    )
    op.create_index("ix_webhook_endpoints_tenant_id", "webhook_endpoints", ["tenant_id"])
    op.create_index(
        "ix_webhook_endpoints_tenant_enabled", "webhook_endpoints", ["tenant_id", "enabled"]
    )

    op.create_table(
        "webhook_deliveries",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "tenant_id", UUID, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "endpoint_id",
            UUID,
            sa.ForeignKey("webhook_endpoints.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event", sa.String(80), nullable=False),
        # pending / success / failed：失败原样记录，不降级成成功。
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("response_code", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_webhook_deliveries_tenant_id", "webhook_deliveries", ["tenant_id"])
    op.create_index(
        "ix_webhook_deliveries_tenant_endpoint_created",
        "webhook_deliveries",
        ["tenant_id", "endpoint_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_webhook_deliveries_tenant_endpoint_created", table_name="webhook_deliveries"
    )
    op.drop_index("ix_webhook_deliveries_tenant_id", table_name="webhook_deliveries")
    op.drop_table("webhook_deliveries")
    op.drop_index("ix_webhook_endpoints_tenant_enabled", table_name="webhook_endpoints")
    op.drop_index("ix_webhook_endpoints_tenant_id", table_name="webhook_endpoints")
    op.drop_table("webhook_endpoints")
