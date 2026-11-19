"""project readiness checklist

Revision ID: 0043_readiness
Revises: 0042_document_kind
Create Date: 2026-10-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID

from alembic import op

revision: str = "0043_readiness"
down_revision: str | None = "0042_document_kind"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "bid_projects", sa.Column("readiness_template_code", sa.String(100), nullable=True)
    )
    op.add_column(
        "bid_projects", sa.Column("readiness_template_version", sa.Integer(), nullable=True)
    )
    op.add_column(
        "bid_projects",
        sa.Column("internal_deadline", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "project_readiness_items",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("bid_projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("template_code", sa.String(100), nullable=False),
        sa.Column("template_version", sa.Integer(), nullable=False),
        sa.Column("item_key", sa.String(100), nullable=False),
        sa.Column("category", sa.String(60), nullable=False, server_default="其他"),
        sa.Column("label", sa.String(300), nullable=False),
        sa.Column("source_type", sa.String(20), nullable=False, server_default="manual"),
        sa.Column("rule_id", sa.String(80), nullable=True),
        sa.Column("requirement_key", sa.String(64), nullable=True),
        sa.Column("finding_id", PGUUID(as_uuid=True), nullable=True),
        sa.Column("is_blocking", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("applicability", sa.String(20), nullable=False, server_default="required"),
        sa.Column("evidence_required", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column(
            "owner_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("collaborator_ids", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("evidence_refs", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("waived_reason", sa.Text(), nullable=True),
        sa.Column(
            "waived_by",
            PGUUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("waived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "confirmed_by",
            PGUUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_state", sa.String(20), nullable=False, server_default="stale"),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stage", sa.String(40), nullable=False, server_default=""),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "project_id", "template_version", "item_key", name="uq_project_readiness_item"
        ),
    )
    op.create_index(
        "ix_project_readiness_tenant_project",
        "project_readiness_items",
        ["tenant_id", "project_id", "status"],
    )
    op.create_index(
        "ix_project_readiness_tenant_owner",
        "project_readiness_items",
        ["tenant_id", "owner_id", "due_at"],
    )


def downgrade() -> None:
    # 共享环境禁止 destructive downgrade；向前修复。
    pass
