"""p2b break glass

Revision ID: 0033_p2b_break_glass
Revises: 0032_p2_org_units
Create Date: 2026-10-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0033_p2b_break_glass"
down_revision: str | None = "0032_p2_org_units"
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
        "compliance:review",
        "compliance:break_glass",
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
        "compliance:review",
        "compliance:break_glass",
    ],
}


def upgrade() -> None:
    op.create_table(
        "compliance_access_requests",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "tenant_id",
            UUID,
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("resource_type", sa.String(40), nullable=False),
        sa.Column("resource_id", UUID, nullable=False),
        sa.Column("requester_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("approver_id", UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("requested_minutes", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_compliance_access_requests_tenant_id",
        "compliance_access_requests",
        ["tenant_id"],
    )
    op.create_index(
        "ix_compliance_access_requests_tenant_status",
        "compliance_access_requests",
        ["tenant_id", "status"],
    )
    op.create_index(
        "ix_compliance_access_requests_tenant_requester",
        "compliance_access_requests",
        ["tenant_id", "requester_id"],
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
