"""p2 persist sync warning details

Revision ID: 0023_p2_sync_warnings
Revises: 0022_p2_fallback_dedup
Create Date: 2026-10-24
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0023_p2_sync_warnings"
down_revision: str | None = "0022_p2_fallback_dedup"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "source_sync_runs",
        sa.Column(
            "warnings_json",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("source_sync_runs", "warnings_json")
