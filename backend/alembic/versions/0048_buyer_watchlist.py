"""buyer watchlist

Revision ID: 0048_buyer_watchlist
Revises: 0047_final_assembly
Create Date: 2026-10-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PGUUID

from alembic import op

revision: str = "0048_buyer_watchlist"
down_revision: str | None = "0047_final_assembly"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "opportunity_buyer_watchlist",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("buyer_name", sa.String(300), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_by",
            PGUUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("tenant_id", "buyer_name", name="uq_opportunity_buyer_watchlist"),
    )
    op.create_index(
        "ix_opportunity_buyer_watchlist_tenant",
        "opportunity_buyer_watchlist",
        ["tenant_id", "created_at"],
    )


def downgrade() -> None:
    # 共享环境禁止 destructive downgrade；向前修复。
    pass
