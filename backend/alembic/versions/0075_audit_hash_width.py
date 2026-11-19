"""audit hash v2 width

审计摘要升级为 "v2:" + HMAC-SHA256（67 字符），原有 64 宽列需扩到 80。

Revision ID: 0075_audit_hash_width
Revises: 0074_eval_constraint
Create Date: 2026-11-08
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0075_audit_hash_width"
down_revision: str | None = "0074_eval_constraint"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("audit_events", "prev_hash", type_=sa.String(length=80))
    op.alter_column("audit_events", "hash", type_=sa.String(length=80))


def downgrade() -> None:
    raise NotImplementedError("forward-only migrations")
