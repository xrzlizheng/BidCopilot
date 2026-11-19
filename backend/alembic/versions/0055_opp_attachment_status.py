"""mark attachment-only opportunity content and clear object-ref bodies

Revision ID: 0055_opp_attachment_status
Revises: 0054_contract_lifecycle
Create Date: 2026-11-01

存量修复：部分公告的正文列里存的是对象存储 key（bid-v3-content/<uuid>/files/<uuid>.pdf），
不是真正的公告正文。这类行统一标记为 attachment 并清空正文列，避免前端把路径当正文渲染。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0055_opp_attachment_status"
down_revision: str | None = "0054_contract_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 无空白 + 含路径分隔符 + 以附件扩展名结尾 => 对象存储 key，而不是公告正文
_REF_PATTERN = r"^[^[:space:]]+/[^[:space:]]+\.(pdf|docx?|xlsx?|zip|ofd)$"


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE opportunities
               SET content_html = NULL,
                   content_text = NULL,
                   content_fetched_at = NULL,
                   detail_status = 'attachment'
             WHERE detail_status = 'full'
               AND (
                    COALESCE(content_text, '') ~ :ref
                 OR COALESCE(content_html, '') ~ :ref
               )
            """
        ).bindparams(ref=_REF_PATTERN)
    )


def downgrade() -> None:
    # 数据修正不可逆：不还原被清空的正文列
    pass