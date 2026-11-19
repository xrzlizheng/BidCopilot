"""source sync run source status

同步运行保留网关 DataResult.status 原值（ok/empty/rate_limited/...），
与运行生命周期 status（queued/running/succeeded/failed）分开存储。

Revision ID: 0072_source_sync_status
Revises: 0071_template_versions
Create Date: 2026-11-08
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0072_source_sync_status"
down_revision: str | None = "0071_template_versions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "source_sync_runs",
        sa.Column("source_status", sa.String(length=20), nullable=True),
    )


def downgrade() -> None:
    raise NotImplementedError("forward-only migrations")
