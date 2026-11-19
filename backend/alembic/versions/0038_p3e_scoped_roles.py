"""p3e scoped roles

Revision ID: 0038_p3e_scoped_roles
Revises: 0037_p3c_custom_roles
Create Date: 2026-10-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0038_p3e_scoped_roles"
down_revision: str | None = "0037_p3c_custom_roles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.add_column(
        "roles",
        sa.Column(
            "scope_types",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[\"tenant\"]'::jsonb"),
        ),
    )
    op.add_column(
        "user_roles",
        sa.Column("scope_type", sa.String(20), nullable=False, server_default="tenant"),
    )
    op.add_column("user_roles", sa.Column("scope_id", UUID, nullable=True))
    op.add_column(
        "user_roles",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute("UPDATE user_roles SET scope_id = tenant_id WHERE scope_id IS NULL")
    op.alter_column("user_roles", "scope_id", nullable=False)
    op.drop_constraint("uq_user_roles_user_role", "user_roles", type_="unique")
    op.create_unique_constraint(
        "uq_user_roles_scoped",
        "user_roles",
        ["user_id", "role_id", "scope_type", "scope_id"],
    )
    op.create_index(
        "ix_user_roles_tenant_user_scope",
        "user_roles",
        ["tenant_id", "user_id", "scope_type"],
    )


def downgrade() -> None:
    # 共享环境禁止 destructive downgrade；向前修复。
    pass
