"""section AIGC metadata

Revision ID: 0045_section_ai_meta
Revises: 0044_project_reminders
Create Date: 2026-10-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0045_section_ai_meta"
down_revision: str | None = "0044_project_reminders"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "bid_sections",
        sa.Column(
            "ai_meta",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    # 共享环境禁止 destructive downgrade；向前修复。
    pass
