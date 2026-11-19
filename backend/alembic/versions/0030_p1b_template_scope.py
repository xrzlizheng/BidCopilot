"""p1b template scope

Revision ID: 0030_p1b_template_scope
Revises: 0029_p1_knowledge_acl
Create Date: 2026-10-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0030_p1b_template_scope"
down_revision: str | None = "0029_p1_knowledge_acl"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.add_column(
        "knowledge_packs",
        sa.Column("scope", sa.String(20), nullable=False, server_default="tenant"),
    )
    op.add_column(
        "knowledge_packs",
        sa.Column("owner_id", UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    )
    op.add_column(
        "knowledge_packs",
        sa.Column("classification", sa.String(20), nullable=False, server_default="internal"),
    )
    op.add_column(
        "knowledge_packs",
        sa.Column("status", sa.String(20), nullable=False, server_default="published"),
    )
    op.add_column(
        "knowledge_packs",
        sa.Column("owner_status", sa.String(20), nullable=False, server_default="active"),
    )
    op.add_column(
        "knowledge_packs",
        sa.Column("published_by", UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    )
    op.add_column(
        "knowledge_packs",
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        "UPDATE knowledge_packs SET scope = CASE WHEN is_builtin THEN 'system' ELSE 'tenant' END"
    )
    op.create_index(
        "ix_knowledge_packs_tenant_scope_owner",
        "knowledge_packs",
        ["tenant_id", "scope", "owner_id"],
    )
    op.create_index(
        "ix_knowledge_packs_tenant_category_status",
        "knowledge_packs",
        ["tenant_id", "category", "status"],
    )


def downgrade() -> None:
    # 共享环境禁止 destructive downgrade；向前修复。
    pass
