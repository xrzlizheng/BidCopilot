"""client scope for agencies

Revision ID: 0057_client_scope
Revises: 0056_opp_attachment_backfill
Create Date: 2026-10-31

代理机构多客户隔离：新增 clients / client_members，并给 companies、
bid_projects、knowledge_bases 增加可空 client_id（NULL = 历史租户级数据）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0057_client_scope"
down_revision: str | None = "0056_opp_attachment_backfill"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "clients",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "tenant_id", UUID, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("credit_code", sa.String(64), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_by", UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("tenant_id", "name", name="uq_clients_tenant_name"),
    )
    op.create_index("ix_clients_tenant_status", "clients", ["tenant_id", "status"])

    op.create_table(
        "client_members",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "tenant_id", UUID, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "client_id", UUID, sa.ForeignKey("clients.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("role", sa.String(20), nullable=False, server_default="member"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "tenant_id", "client_id", "user_id", name="uq_client_members"
        ),
    )
    op.create_index(
        "ix_client_members_tenant_user", "client_members", ["tenant_id", "user_id"]
    )

    for table in ("companies", "bid_projects", "knowledge_bases"):
        op.add_column(table, sa.Column("client_id", UUID, nullable=True))
        op.create_foreign_key(
            f"fk_{table}_client_id",
            table,
            "clients",
            ["client_id"],
            ["id"],
            ondelete="SET NULL",
        )
        op.create_index(f"ix_{table}_client_id", table, ["client_id"])


def downgrade() -> None:
    for table in ("companies", "bid_projects", "knowledge_bases"):
        op.drop_index(f"ix_{table}_client_id", table_name=table)
        op.drop_constraint(f"fk_{table}_client_id", table, type_="foreignkey")
        op.drop_column(table, "client_id")
    op.drop_index("ix_client_members_tenant_user", table_name="client_members")
    op.drop_table("client_members")
    op.drop_index("ix_clients_tenant_status", table_name="clients")
    op.drop_table("clients")
