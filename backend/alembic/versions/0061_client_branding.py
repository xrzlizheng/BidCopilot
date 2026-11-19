"""client white-label branding

Revision ID: 0061_client_branding
Revises: 0060_submission_receipts
Create Date: 2026-10-31
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0061_client_branding"
down_revision: str | None = "0060_submission_receipts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("clients", sa.Column("brand_name", sa.String(200), nullable=True))
    op.add_column("clients", sa.Column("brand_logo_url", sa.String(500), nullable=True))


def downgrade() -> None:
    op.drop_column("clients", "brand_logo_url")
    op.drop_column("clients", "brand_name")
