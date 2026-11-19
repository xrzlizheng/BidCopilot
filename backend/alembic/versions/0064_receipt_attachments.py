"""submission receipt attachments

交标回执留痕：平台回执截图/PDF、CA 签章凭证等附件与回执一并冻结，作为未自动化的
「递交动作」的人工证据。附件内容落对象存储（snapshots 同源组件），库里只存引用与哈希。

Revision ID: 0064_receipt_attachments
Revises: 0063_client_template_scope
Create Date: 2026-10-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0064_receipt_attachments"
down_revision: str | None = "0063_client_template_scope"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "submission_receipt_attachments",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "tenant_id", UUID, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "receipt_id",
            UUID,
            sa.ForeignKey("submission_receipts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(40), nullable=False, server_default="receipt"),
        sa.Column("filename", sa.String(500), nullable=False),
        sa.Column("content_type", sa.String(200), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("storage_ref", sa.String(500), nullable=False),
        sa.Column(
            "uploaded_by", UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "ix_receipt_attachments_tenant_receipt",
        "submission_receipt_attachments",
        ["tenant_id", "receipt_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_receipt_attachments_tenant_receipt", table_name="submission_receipt_attachments"
    )
    op.drop_table("submission_receipt_attachments")
