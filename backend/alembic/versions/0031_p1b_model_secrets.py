"""p1b model secrets

Revision ID: 0031_p1b_model_secrets
Revises: 0030_p1b_template_scope
Create Date: 2026-10-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0031_p1b_model_secrets"
down_revision: str | None = "0030_p1b_template_scope"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)

ROLE_PERMISSIONS = {
    "tenant_admin": [
        "tenant:read",
        "user:manage",
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
    ],
}


def upgrade() -> None:
    op.create_table(
        "secret_store",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "tenant_id",
            UUID,
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("scope", sa.String(20), nullable=False),
        sa.Column("owner_id", UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("provider", sa.String(40), nullable=False, server_default="openai_compatible"),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("nonce", sa.LargeBinary(), nullable=False),
        sa.Column("wrapped_dek", sa.LargeBinary(), nullable=False),
        sa.Column("dek_nonce", sa.LargeBinary(), nullable=False),
        sa.Column("key_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("last4", sa.String(4), nullable=False),
        sa.Column("created_by", UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_secret_store_tenant_id", "secret_store", ["tenant_id"])
    op.create_index("ix_secret_store_tenant_scope", "secret_store", ["tenant_id", "scope"])
    op.create_index("ix_secret_store_tenant_owner", "secret_store", ["tenant_id", "owner_id"])

    op.add_column(
        "model_configs",
        sa.Column("scope", sa.String(20), nullable=False, server_default="tenant"),
    )
    op.add_column(
        "model_configs",
        sa.Column("owner_id", UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    )
    op.add_column(
        "model_configs",
        sa.Column(
            "secret_ref",
            UUID,
            sa.ForeignKey("secret_store.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column("model_configs", sa.Column("key_last4", sa.String(4), nullable=True))

    op.drop_constraint(
        "uq_model_configs_tenant_purpose", "model_configs", type_="unique"
    )
    op.create_index(
        "uq_model_configs_tenant_purpose",
        "model_configs",
        ["tenant_id", "purpose"],
        unique=True,
        postgresql_where=sa.text("scope = 'tenant'"),
    )
    op.create_index(
        "uq_model_configs_personal_purpose",
        "model_configs",
        ["tenant_id", "owner_id", "purpose"],
        unique=True,
        postgresql_where=sa.text("scope = 'personal'"),
    )
    op.create_index("ix_model_configs_tenant_scope", "model_configs", ["tenant_id", "scope"])

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
