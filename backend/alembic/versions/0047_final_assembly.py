"""final document assembly metadata

Revision ID: 0047_final_assembly
Revises: 0046_decision_runs
Create Date: 2026-10-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0047_final_assembly"
down_revision: str | None = "0046_decision_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tender_documents",
        sa.Column(
            "assembly_meta",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    # 共享环境禁止 destructive downgrade；向前修复。
    pass
