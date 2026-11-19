"""p1 ocr provenance

Revision ID: 0009_p1_ocr_provenance
Revises: 0008_p1_bid_results
Create Date: 2026-10-21
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0009_p1_ocr_provenance"
down_revision: str | None = "0008_p1_bid_results"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tender_documents",
        sa.Column("ocr_used", sa.Boolean, nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column("tender_documents", "ocr_used")
