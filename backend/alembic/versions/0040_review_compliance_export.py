"""review compliance export capability

Revision ID: 0040_review_compliance_export
Revises: 0039_review_unique_guards
Create Date: 2026-10-25
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0040_review_compliance_export"
down_revision: str | None = "0039_review_unique_guards"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE roles
        SET permissions = permissions || '["compliance:export"]'::jsonb
        WHERE tenant_id IS NULL
          AND code IN ('tenant_admin', 'compliance')
          AND NOT permissions @> '["compliance:export"]'::jsonb
        """
    )


def downgrade() -> None:
    # 共享环境禁止 destructive downgrade；向前修复。
    pass
