"""p3 platform: tender document versions + async parse jobs (stage 7)

Revision ID: 0026_p3_doc_versions
Revises: 0025_p3_contract_ops
Create Date: 2026-10-24
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0026_p3_doc_versions"
down_revision: str | None = "0025_p3_contract_ops"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB


def upgrade() -> None:
    # 7.2 Work 身份：招标文档版本链
    op.add_column("tender_documents", sa.Column("version", sa.Integer, nullable=False, server_default="1"))
    op.add_column(
        "tender_documents",
        sa.Column(
            "supersedes_id",
            UUID,
            sa.ForeignKey("tender_documents.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_tender_documents_project_version", "tender_documents", ["project_id", "version"])

    # 7.3 长文档异步解析任务
    op.create_table(
        "parse_jobs",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", UUID, sa.ForeignKey("bid_projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("document_id", UUID, sa.ForeignKey("tender_documents.id", ondelete="CASCADE"), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("progress", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("created_by", UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_parse_jobs_tenant_status", "parse_jobs", ["tenant_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_parse_jobs_tenant_status", table_name="parse_jobs")
    op.drop_table("parse_jobs")
    op.drop_index("ix_tender_documents_project_version", table_name="tender_documents")
    op.drop_column("tender_documents", "supersedes_id")
    op.drop_column("tender_documents", "version")
