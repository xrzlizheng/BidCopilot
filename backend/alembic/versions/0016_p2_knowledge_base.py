"""p2 enterprise knowledge base

Revision ID: 0016_p2_knowledge_base
Revises: 0015_p2_model_configs
Create Date: 2026-10-22
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0016_p2_knowledge_base"
down_revision: str | None = "0015_p2_model_configs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "knowledge_documents",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("company_id", UUID, sa.ForeignKey("companies.id", ondelete="SET NULL"), nullable=True),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False, server_default="other"),
        sa.Column("filename", sa.String(300), nullable=False),
        sa.Column("content_type", sa.String(120), nullable=True),
        sa.Column("size_bytes", sa.Integer, nullable=False),
        sa.Column("file_hash", sa.String(64), nullable=False),
        sa.Column("object_ref", sa.Text, nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="parsed"),
        sa.Column("text_content", sa.Text, nullable=False, server_default=""),
        sa.Column("tags", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("created_by", UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index(
        "ix_knowledge_documents_tenant_created", "knowledge_documents", ["tenant_id", "created_at"]
    )
    op.create_index(
        "ix_knowledge_documents_tenant_hash", "knowledge_documents", ["tenant_id", "file_hash"]
    )

    op.create_table(
        "knowledge_chunks",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "document_id",
            UUID,
            sa.ForeignKey("knowledge_documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chunk_index", sa.Integer, nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("heading", sa.String(300), nullable=True),
        sa.Column("keywords", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    op.create_index(
        "ix_knowledge_chunks_tenant_document", "knowledge_chunks", ["tenant_id", "document_id"]
    )
    op.create_index(
        "ix_knowledge_chunks_keywords", "knowledge_chunks", ["keywords"], postgresql_using="gin"
    )

    op.create_table(
        "knowledge_images",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("company_id", UUID, sa.ForeignKey("companies.id", ondelete="SET NULL"), nullable=True),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("caption", sa.Text, nullable=True),
        sa.Column("tags", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("filename", sa.String(300), nullable=False),
        sa.Column("content_type", sa.String(120), nullable=True),
        sa.Column("size_bytes", sa.Integer, nullable=False),
        sa.Column("file_hash", sa.String(64), nullable=False),
        sa.Column("object_ref", sa.Text, nullable=False),
        sa.Column("width", sa.Integer, nullable=True),
        sa.Column("height", sa.Integer, nullable=True),
        sa.Column("sensitive", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("created_by", UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index(
        "ix_knowledge_images_tenant_created", "knowledge_images", ["tenant_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_table("knowledge_images")
    op.drop_table("knowledge_chunks")
    op.drop_table("knowledge_documents")
