"""template (knowledge pack) client scope

代理机构多客户隔离的第二阶段：模板归属客户，使「按客户交付」的模板不可跨客户可见。

Revision ID: 0063_client_template_scope
Revises: 0062_notification_channels
Create Date: 2026-10-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0063_client_template_scope"
down_revision: str | None = "0062_notification_channels"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.add_column("knowledge_packs", sa.Column("client_id", UUID, nullable=True))
    op.create_foreign_key(
        "fk_knowledge_packs_client_id",
        "knowledge_packs",
        "clients",
        ["client_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_knowledge_packs_client_id", "knowledge_packs", ["client_id"])


def downgrade() -> None:
    op.drop_index("ix_knowledge_packs_client_id", table_name="knowledge_packs")
    op.drop_constraint("fk_knowledge_packs_client_id", "knowledge_packs", type_="foreignkey")
    op.drop_column("knowledge_packs", "client_id")
