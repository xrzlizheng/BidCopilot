"""p2 org units

Revision ID: 0032_p2_org_units
Revises: 0031_p1b_model_secrets
Create Date: 2026-10-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0032_p2_org_units"
down_revision: str | None = "0031_p1b_model_secrets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)

ROLE_PERMISSIONS = {
    "tenant_admin": [
        "tenant:read",
        "user:manage",
        "org:read",
        "org:manage",
        "asset:*",
        "opportunity:*",
        "project:*",
        "knowledge:*",
        "template:*",
        "model:use",
        "model:read_metadata",
        "model:manage",
        "model:manage_key",
        "model:policy:manage",
        "audit:read",
    ],
    "bid_manager": [
        "opportunity:*",
        "asset:read",
        "project:*",
        "knowledge:read",
        "knowledge:use",
        "knowledge:write",
        "template:read",
        "template:use",
        "template:copy",
        "template:write",
        "model:use",
        "org:read",
    ],
    "bid_engineer": [
        "opportunity:read",
        "asset:read",
        "project:read",
        "project:write",
        "knowledge:read",
        "knowledge:use",
        "knowledge:write",
        "template:read",
        "template:use",
        "template:copy",
        "template:write",
        "model:use",
        "org:read",
    ],
    "compliance": [
        "opportunity:read",
        "asset:read",
        "project:read",
        "project:approve",
        "audit:read",
        "knowledge:read",
        "knowledge:use",
        "template:read",
        "template:use",
        "template:copy",
        "model:use",
        "org:read",
    ],
}


def upgrade() -> None:
    op.create_table(
        "org_units",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "tenant_id",
            UUID,
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("parent_id", UUID, sa.ForeignKey("org_units.id", ondelete="SET NULL"), nullable=True),
        sa.Column("kind", sa.String(20), nullable=False, server_default="department"),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("path", sa.String(500), nullable=False, server_default=""),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("created_by", UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_org_units_tenant_id", "org_units", ["tenant_id"])
    op.create_index("ix_org_units_tenant_parent", "org_units", ["tenant_id", "parent_id"])
    op.create_index("ix_org_units_tenant_path", "org_units", ["tenant_id", "path"])

    op.create_table(
        "org_members",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "tenant_id",
            UUID,
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("org_unit_id", UUID, sa.ForeignKey("org_units.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("member_role", sa.String(20), nullable=False, server_default="member"),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("org_unit_id", "user_id", name="uq_org_members_unit_user"),
    )
    op.create_index("ix_org_members_tenant_id", "org_members", ["tenant_id"])
    op.create_index("ix_org_members_tenant_user", "org_members", ["tenant_id", "user_id"])

    op.add_column("knowledge_bases", sa.Column("org_unit_id", UUID, nullable=True))
    op.create_foreign_key(
        "fk_knowledge_bases_org_unit",
        "knowledge_bases",
        "org_units",
        ["org_unit_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_knowledge_bases_tenant_org_unit", "knowledge_bases", ["tenant_id", "org_unit_id"]
    )

    roles = sa.table(
        "roles",
        sa.column("code", sa.String),
        sa.column("permissions", sa.JSON),
        sa.column("tenant_id", sa.UUID),
    )
    for code, permissions in ROLE_PERMISSIONS.items():
        op.execute(
            roles.update()
            .where(roles.c.code == code, roles.c.tenant_id.is_(None))
            .values(permissions=permissions)
        )


def downgrade() -> None:
    # 共享环境禁止 destructive downgrade；向前修复。
    pass
