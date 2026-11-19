"""intel watchlist for enterprise monitoring

Revision ID: 0058_intel_watchlist
Revises: 0057_client_scope
Create Date: 2026-10-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0058_intel_watchlist"
down_revision: str | None = "0057_client_scope"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB


def upgrade() -> None:
    op.create_table(
        "intel_watchlist",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "tenant_id", UUID, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "client_id", UUID, sa.ForeignKey("clients.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("ent_name", sa.String(300), nullable=False),
        sa.Column("label", sa.String(300), nullable=True),
        sa.Column(
            "sections",
            sa.String(400),
            nullable=False,
            server_default="enforced,dishonest,admin_penalty,abnormal_operation",
        ),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_status", sa.String(20), nullable=True),
        sa.Column("last_digest", JSONB, nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_by", UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("tenant_id", "ent_name", name="uq_intel_watchlist_tenant_ent"),
    )
    op.create_index(
        "ix_intel_watchlist_tenant_active", "intel_watchlist", ["tenant_id", "active"]
    )


def downgrade() -> None:
    op.drop_index("ix_intel_watchlist_tenant_active", table_name="intel_watchlist")
    op.drop_table("intel_watchlist")
