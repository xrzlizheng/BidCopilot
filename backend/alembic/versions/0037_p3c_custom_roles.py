"""p3c custom roles

Revision ID: 0037_p3c_custom_roles
Revises: 0036_p3b_field_policies
Create Date: 2026-10-24
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0037_p3c_custom_roles"
down_revision: str | None = "0036_p3b_field_policies"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_ADMIN_PERMISSIONS = [
    "tenant:read",
    "user:manage",
    "role:read",
    "role:manage",
    "role:assign",
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
    "compliance:review",
    "compliance:break_glass",
]


def upgrade() -> None:
    roles = sa.table(
        "roles",
        sa.column("code", sa.String),
        sa.column("permissions", sa.JSON),
        sa.column("tenant_id", sa.UUID),
    )
    op.execute(
        roles.update()
        .where(roles.c.code == "tenant_admin", roles.c.tenant_id.is_(None))
        .values(permissions=TENANT_ADMIN_PERMISSIONS)
    )


def downgrade() -> None:
    # 共享环境禁止 destructive downgrade；向前修复。
    pass
