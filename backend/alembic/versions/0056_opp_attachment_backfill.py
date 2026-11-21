r"""re-run attachment backfill with corrected regex

Revision ID: 0056_opp_attachment_backfill
Revises: 0055_opp_attachment_status
Create Date: 2026-11-01

0055 的早期版本在正则里把 `\.` 误写成 `\\.`，导致回填没有命中任何行；
本迁移用正确的正则重跑一次，幂等，可在已升级环境上收敛。
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0056_opp_attachment_backfill"
down_revision: str | None = "0055_opp_attachment_status"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BACKFILL_SQL = r"""
UPDATE opportunities
   SET content_html = NULL,
       content_text = NULL,
       content_fetched_at = NULL,
       detail_status = 'attachment'
 WHERE detail_status = 'full'
   AND (
        COALESCE(content_text, '') ~ '^[^[:space:]]+/[^[:space:]]+\.(pdf|docx?|xlsx?|zip|ofd)$'
     OR COALESCE(content_html, '') ~ '^[^[:space:]]+/[^[:space:]]+\.(pdf|docx?|xlsx?|zip|ofd)$'
   )
"""


def upgrade() -> None:
    op.execute(_BACKFILL_SQL)


def downgrade() -> None:
    # 数据修正不可逆
    pass
