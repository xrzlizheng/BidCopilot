"""notification channels

Revision ID: 0062_notification_channels
Revises: 0061_client_branding
Create Date: 2026-10-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0062_notification_channels"
down_revision: str | None = "0061_client_branding"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "notification_channels",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "tenant_id", UUID, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("target", sa.String(500), nullable=False),
        sa.Column("label", sa.String(200), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_by", UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("tenant_id", "kind", "target", name="uq_notification_channels"),
    )


def downgrade() -> None:
    op.drop_table("notification_channels")
