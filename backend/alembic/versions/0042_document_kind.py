"""document kind for tender/clarification/amendment

Revision ID: 0042_document_kind
Revises: 0041_paid_call_quotas
Create Date: 2026-10-25
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0042_document_kind"
down_revision: str | None = "0041_paid_call_quotas"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tender_documents",
        sa.Column(
            "document_kind",
            sa.String(20),
            nullable=False,
            server_default="tender",
        ),
    )


def downgrade() -> None:
    # 共享环境禁止 destructive downgrade；向前修复。
    pass
