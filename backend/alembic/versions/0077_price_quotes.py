"""price quotes

报价单不可变快照：总价/明细/大写金额/有效期，定价依据指向 price_strategies。

Revision ID: 0077_price_quotes
Revises: 0076_webhook_outbox
Create Date: 2026-11-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0077_price_quotes"
down_revision: str | None = "0076_webhook_outbox"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "price_quotes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("strategy_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("quote_number", sa.String(length=60), nullable=False),
        sa.Column("project_name", sa.String(length=400), nullable=False),
        sa.Column("buyer_name", sa.Text(), nullable=True),
        sa.Column("supplier_name", sa.String(length=300), nullable=True),
        sa.Column("total_amount", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=10), nullable=False, server_default="CNY"),
        sa.Column("amount_in_words", sa.String(length=200), nullable=False),
        sa.Column(
            "items_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("tier", sa.String(length=20), nullable=True),
        sa.Column("price_source", sa.String(length=40), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["bid_projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["strategy_id"], ["price_strategies.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_price_quotes_tenant_project",
        "price_quotes",
        ["tenant_id", "project_id", "created_at"],
    )


def downgrade() -> None:
    raise NotImplementedError("forward-only migrations")