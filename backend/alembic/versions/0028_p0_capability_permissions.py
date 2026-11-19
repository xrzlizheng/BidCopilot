"""p0 capability permissions

Revision ID: 0028_p0_capability_permissions
Revises: 0027_p2_policy_threshold
Create Date: 2026-10-24
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0028_p0_capability_permissions"
down_revision: str | None = "0027_p2_policy_threshold"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLE_PERMISSIONS = {
    "tenant_admin": [
        "tenant:read",
        "user:manage",
        "asset:*",
        "opportunity:*",
        "project:*",
        "knowledge:*",
        "template:*",
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
    ],
}


def upgrade() -> None:
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
    # 共享环境禁止 destructive downgrade；系统角色权限可向前修复。
    pass
