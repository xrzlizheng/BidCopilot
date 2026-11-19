"""webhook delivery outbox

投递事件载荷持久化 + 下次重试时间：进程重启后由调度器补投，不丢事件。

Revision ID: 0076_webhook_outbox
Revises: 0075_audit_hash_width
Create Date: 2026-11-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0076_webhook_outbox"
down_revision: str | None = "0075_audit_hash_width"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "webhook_deliveries",
        sa.Column(
            "payload_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "webhook_deliveries",
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_webhook_deliveries_next_retry",
        "webhook_deliveries",
        ["next_retry_at"],
    )


def downgrade() -> None:
    raise NotImplementedError("forward-only migrations")
