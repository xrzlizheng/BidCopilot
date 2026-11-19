"""bid commitments for contract handoff

Revision ID: 0049_bid_commitments
Revises: 0048_buyer_watchlist
Create Date: 2026-10-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PGUUID

from alembic import op

revision: str = "0049_bid_commitments"
down_revision: str | None = "0048_buyer_watchlist"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "bid_commitments",
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
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("value_text", sa.Text(), nullable=False),
        sa.Column("value_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("value_days", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(40), nullable=False, server_default="manual"),
        sa.Column(
            "confirmed_by",
            PGUUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("project_id", "kind", "label", name="uq_bid_commitment"),
    )
    op.create_index(
        "ix_bid_commitments_tenant_project",
        "bid_commitments",
        ["tenant_id", "project_id"],
    )


def downgrade() -> None:
    # 共享环境禁止 destructive downgrade；向前修复。
    pass
