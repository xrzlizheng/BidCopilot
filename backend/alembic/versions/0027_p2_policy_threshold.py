"""p2 compliance policy threshold override

Revision ID: 0027_p2_policy_threshold
Revises: 0026_p3_doc_versions
Create Date: 2026-10-24
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0027_p2_policy_threshold"
down_revision: str | None = "0026_p3_doc_versions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "policies",
        sa.Column("threshold_override", sa.Float, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("policies", "threshold_override")
