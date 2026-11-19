"""p1 knowledge packs and project templates

Revision ID: 0013_p1_templates
Revises: 0012_p0_token_version
Create Date: 2026-10-21
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0013_p1_templates"
down_revision: str | None = "0012_p0_token_version"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "knowledge_packs",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True),
        sa.Column("code", sa.String(100), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("category", sa.String(30), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("content_json", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("version", sa.String(40), nullable=False, server_default="1.0"),
        sa.Column("is_builtin", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("code", name="uq_knowledge_packs_code"),
    )
    op.create_index("ix_knowledge_packs_tenant_id", "knowledge_packs", ["tenant_id"])
    op.create_index("ix_knowledge_packs_tenant_category", "knowledge_packs", ["tenant_id", "category"])
    op.add_column("bid_projects", sa.Column("template_code", sa.String(100), nullable=True))
    op.add_column("bid_projects", sa.Column("format_code", sa.String(100), nullable=True))


def downgrade() -> None:
    op.drop_column("bid_projects", "format_code")
    op.drop_column("bid_projects", "template_code")
    op.drop_table("knowledge_packs")
