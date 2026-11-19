"""submission receipts

Revision ID: 0060_submission_receipts
Revises: 0058_intel_watchlist
Create Date: 2026-10-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0060_submission_receipts"
down_revision: str | None = "0058_intel_watchlist"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB


def upgrade() -> None:
    op.create_table(
        "submission_receipts",
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
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("platform", sa.String(100), nullable=False),
        sa.Column("receipt_no", sa.String(200), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "operator_id", UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("checklist_snapshot", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "ix_submission_receipts_tenant_project",
        "submission_receipts",
        ["tenant_id", "project_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_submission_receipts_tenant_project", table_name="submission_receipts"
    )
    op.drop_table("submission_receipts")
