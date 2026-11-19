"""p1 knowledge acl

Revision ID: 0029_p1_knowledge_acl
Revises: 0028_p0_capability_permissions
Create Date: 2026-10-24
"""

from collections.abc import Sequence
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0029_p1_knowledge_acl"
down_revision: str | None = "0028_p0_capability_permissions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("clearance", sa.String(20), nullable=False, server_default="internal"),
    )

    op.create_table(
        "knowledge_bases",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "tenant_id",
            UUID,
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("scope", sa.String(20), nullable=False, server_default="tenant"),
        sa.Column("owner_id", UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "classification", sa.String(20), nullable=False, server_default="internal"
        ),
        sa.Column("visibility", sa.String(20), nullable=False, server_default="enterprise"),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("owner_status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("acl_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by", UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_knowledge_bases_tenant_id", "knowledge_bases", ["tenant_id"])
    op.create_index(
        "ix_knowledge_bases_tenant_scope", "knowledge_bases", ["tenant_id", "scope"]
    )
    op.create_index(
        "ix_knowledge_bases_tenant_owner", "knowledge_bases", ["tenant_id", "owner_id"]
    )

    op.create_table(
        "resource_acl",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "tenant_id",
            UUID,
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("resource_type", sa.String(60), nullable=False),
        sa.Column("resource_id", UUID, nullable=False),
        sa.Column("subject_type", sa.String(20), nullable=False),
        sa.Column("subject_id", UUID, nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column(
            "include_descendants", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("granted_by", UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "tenant_id",
            "resource_type",
            "resource_id",
            "subject_type",
            "subject_id",
            name="uq_resource_acl_subject",
        ),
    )
    op.create_index("ix_resource_acl_tenant_id", "resource_acl", ["tenant_id"])
    op.create_index(
        "ix_resource_acl_tenant_subject",
        "resource_acl",
        ["tenant_id", "subject_type", "subject_id", "resource_type"],
    )
    op.create_index(
        "ix_resource_acl_tenant_resource",
        "resource_acl",
        ["tenant_id", "resource_type", "resource_id"],
    )

    op.add_column("knowledge_documents", sa.Column("kb_id", UUID, nullable=True))
    op.add_column(
        "knowledge_documents",
        sa.Column("classification", sa.String(20), nullable=False, server_default="internal"),
    )
    op.add_column(
        "knowledge_documents",
        sa.Column("acl_version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column("knowledge_chunks", sa.Column("kb_id", UUID, nullable=True))
    op.add_column(
        "knowledge_chunks",
        sa.Column("classification", sa.String(20), nullable=False, server_default="internal"),
    )
    op.add_column("knowledge_images", sa.Column("kb_id", UUID, nullable=True))
    op.add_column(
        "knowledge_images",
        sa.Column("classification", sa.String(20), nullable=False, server_default="internal"),
    )

    bind = op.get_bind()
    tenant_rows = bind.execute(sa.text("SELECT id FROM tenants")).fetchall()
    for (tenant_id,) in tenant_rows:
        kb_id = uuid4()
        bind.execute(
            sa.text(
                """
                INSERT INTO knowledge_bases
                    (id, tenant_id, scope, owner_id, name, description, classification,
                     visibility, status, owner_status, acl_version, created_by, created_at)
                VALUES
                    (:id, :tenant_id, 'tenant', NULL, '企业知识库', NULL, 'internal',
                     'enterprise', 'active', 'active', 1, NULL, now())
                """
            ),
            {"id": kb_id, "tenant_id": tenant_id},
        )
        bind.execute(
            sa.text(
                "UPDATE knowledge_documents SET kb_id = :kb_id "
                "WHERE tenant_id = :tenant_id AND kb_id IS NULL"
            ),
            {"kb_id": kb_id, "tenant_id": tenant_id},
        )
        bind.execute(
            sa.text(
                "UPDATE knowledge_images SET kb_id = :kb_id "
                "WHERE tenant_id = :tenant_id AND kb_id IS NULL"
            ),
            {"kb_id": kb_id, "tenant_id": tenant_id},
        )

    bind.execute(
        sa.text(
            """
            UPDATE knowledge_chunks AS c
            SET kb_id = d.kb_id, classification = d.classification
            FROM knowledge_documents AS d
            WHERE c.document_id = d.id
              AND c.tenant_id = d.tenant_id
              AND c.kb_id IS NULL
            """
        )
    )

    op.alter_column("knowledge_documents", "kb_id", nullable=False)
    op.alter_column("knowledge_chunks", "kb_id", nullable=False)
    op.alter_column("knowledge_images", "kb_id", nullable=False)
    op.create_foreign_key(
        "fk_knowledge_documents_kb",
        "knowledge_documents",
        "knowledge_bases",
        ["kb_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_knowledge_chunks_kb",
        "knowledge_chunks",
        "knowledge_bases",
        ["kb_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_knowledge_images_kb",
        "knowledge_images",
        "knowledge_bases",
        ["kb_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_knowledge_documents_tenant_kb", "knowledge_documents", ["tenant_id", "kb_id"]
    )
    op.create_index(
        "ix_knowledge_chunks_tenant_kb", "knowledge_chunks", ["tenant_id", "kb_id"]
    )
    op.create_index(
        "ix_knowledge_images_tenant_kb", "knowledge_images", ["tenant_id", "kb_id"]
    )


def downgrade() -> None:
    # 共享环境禁止 destructive downgrade；向前修复。
    pass
