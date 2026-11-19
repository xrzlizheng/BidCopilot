"""p2 project package code

Revision ID: 0018_p2_project_package
Revises: 0017_p2_compliance_responses
Create Date: 2026-10-23
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0018_p2_project_package"
down_revision: str | None = "0017_p2_compliance_responses"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("bid_projects", sa.Column("package_code", sa.String(100), nullable=True))


def downgrade() -> None:
    op.drop_column("bid_projects", "package_code")
