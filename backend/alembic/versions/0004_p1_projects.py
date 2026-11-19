"""p1 projects and tender intelligence schema

Revision ID: 0004_p1_projects
Revises: 0003_p0_assets
Create Date: 2026-10-21
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0004_p1_projects"
down_revision: str | None = "0003_p0_assets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "bid_projects",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("opportunity_id", UUID, sa.ForeignKey("opportunities.id", ondelete="SET NULL"), nullable=True),
        sa.Column("company_id", UUID, sa.ForeignKey("companies.id", ondelete="SET NULL"), nullable=True),
        sa.Column("name", sa.String(400), nullable=False),
        sa.Column("code", sa.String(200), nullable=True),
        sa.Column("buyer", sa.Text, nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("owner_id", UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("bid_deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_status", sa.String(20), nullable=True),
        sa.Column("decision_json", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("decision_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_by", UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_bid_projects_tenant_id", "bid_projects", ["tenant_id"])
    op.create_index("ix_bid_projects_tenant_status", "bid_projects", ["tenant_id", "status"])
    op.create_index("ix_bid_projects_tenant_created", "bid_projects", ["tenant_id", "created_at"])
    op.create_table(
        "tender_documents",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", UUID, sa.ForeignKey("bid_projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("filename", sa.String(300), nullable=False),
        sa.Column("content_type", sa.String(120), nullable=True),
        sa.Column("size_bytes", sa.Integer, nullable=False),
        sa.Column("file_hash", sa.String(64), nullable=False),
        sa.Column("object_ref", sa.Text, nullable=False),
        sa.Column("parse_status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("parse_error", sa.Text, nullable=True),
        sa.Column("parsed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_tender_documents_tenant_id", "tender_documents", ["tenant_id"])
    op.create_index("ix_tender_documents_tenant_project", "tender_documents", ["tenant_id", "project_id"])
    op.create_table(
        "tender_constraints",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", UUID, sa.ForeignKey("bid_projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("document_id", UUID, sa.ForeignKey("tender_documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("payload", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("parse_meta", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("project_id", "version", name="uq_tender_constraints_project_version"),
    )
    op.create_index("ix_tender_constraints_tenant_id", "tender_constraints", ["tenant_id"])
    op.create_table(
        "scoring_items",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", UUID, sa.ForeignKey("bid_projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("constraint_id", UUID, sa.ForeignKey("tender_constraints.id", ondelete="CASCADE"), nullable=False),
        sa.Column("category", sa.String(40), nullable=False, server_default="其他"),
        sa.Column("item", sa.Text, nullable=False),
        sa.Column("score", sa.Numeric(8, 2), nullable=True),
        sa.Column("criteria", sa.Text, nullable=True),
        sa.Column("source_span", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("confidence", sa.String(10), nullable=False, server_default="medium"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_scoring_items_tenant_id", "scoring_items", ["tenant_id"])
    op.create_index("ix_scoring_items_tenant_project", "scoring_items", ["tenant_id", "project_id"])
    op.create_table(
        "response_matrix",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", UUID, sa.ForeignKey("bid_projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scoring_item_id", UUID, sa.ForeignKey("scoring_items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("response_strategy", sa.Text, nullable=False),
        sa.Column("evidence_required", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("matched_assets", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("missing_evidence", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("section_mapping", sa.String(100), nullable=True),
        sa.Column("coverage_status", sa.String(20), nullable=False, server_default="missing"),
        sa.Column("risk_note", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("scoring_item_id", name="uq_response_matrix_scoring_item"),
    )
    op.create_index("ix_response_matrix_tenant_id", "response_matrix", ["tenant_id"])
    op.create_index("ix_response_matrix_tenant_project", "response_matrix", ["tenant_id", "project_id"])


def downgrade() -> None:
    op.drop_table("response_matrix")
    op.drop_table("scoring_items")
    op.drop_table("tender_constraints")
    op.drop_table("tender_documents")
    op.drop_table("bid_projects")
