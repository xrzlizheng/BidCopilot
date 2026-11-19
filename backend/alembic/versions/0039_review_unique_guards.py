"""review unique guards

Revision ID: 0039_review_unique_guards
Revises: 0038_p3e_scoped_roles
Create Date: 2026-10-25
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0039_review_unique_guards"
down_revision: str | None = "0038_p3e_scoped_roles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "uq_knowledge_bases_default",
        "knowledge_bases",
        ["tenant_id"],
        unique=True,
        postgresql_where=sa.text(
            "scope = 'tenant' AND name = '企业知识库' AND status = 'active'"
        ),
    )


def downgrade() -> None:
    # 共享环境禁止 destructive downgrade；向前修复。
    pass
