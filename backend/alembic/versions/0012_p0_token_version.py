"""p0 user token version

Revision ID: 0012_p0_token_version
Revises: 0011_p1_cost_ledger
Create Date: 2026-10-21
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0012_p0_token_version"
down_revision: str | None = "0011_p1_cost_ledger"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("token_version", sa.Integer, nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("users", "token_version")
