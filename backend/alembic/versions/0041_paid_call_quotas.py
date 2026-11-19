"""paid call quotas

Revision ID: 0041_paid_call_quotas
Revises: 0040_review_compliance_export
Create Date: 2026-10-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PGUUID

from alembic import op

revision: str = "0041_paid_call_quotas"
down_revision: str | None = "0040_review_compliance_export"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "paid_call_quotas",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("period", sa.String(10), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "period",
            "period_start",
            name="uq_paid_call_quota_period",
        ),
    )
    op.create_index(
        "ix_paid_call_quotas_tenant_id", "paid_call_quotas", ["tenant_id"]
    )


def downgrade() -> None:
    # 共享环境禁止 destructive downgrade；向前修复。
    pass
