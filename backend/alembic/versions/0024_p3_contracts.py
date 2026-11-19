"""p3 contract review (stage 5.1: reviews / clauses / findings)

Revision ID: 0024_p3_contracts
Revises: 0023_p2_sync_warnings
Create Date: 2026-10-24
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0024_p3_contracts"
down_revision: str | None = "0023_p2_sync_warnings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB


def upgrade() -> None:
    op.create_table(
        "contract_reviews",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", UUID, sa.ForeignKey("bid_projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("filename", sa.String(300), nullable=False),
        sa.Column("size_bytes", sa.Integer, nullable=False),
        sa.Column("file_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="reviewed"),
        sa.Column("summary_json", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_by", UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("tenant_id", "project_id", "file_hash", name="uq_contract_reviews_hash"),
    )
    op.create_index("ix_contract_reviews_tenant_project", "contract_reviews", ["tenant_id", "project_id"])

    op.create_table(
        "contract_clauses",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("review_id", UUID, sa.ForeignKey("contract_reviews.id", ondelete="CASCADE"), nullable=False),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("category", sa.String(40), nullable=False, server_default="other"),
        sa.Column("page", sa.Integer, nullable=True),
        sa.Column("source_span", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_contract_clauses_review", "contract_clauses", ["review_id", "seq"])

    op.create_table(
        "contract_findings",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("review_id", UUID, sa.ForeignKey("contract_reviews.id", ondelete="CASCADE"), nullable=False),
        sa.Column("clause_id", UUID, sa.ForeignKey("contract_clauses.id", ondelete="CASCADE"), nullable=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("rule_id", sa.String(60), nullable=True),
        sa.Column("severity", sa.String(10), nullable=False, server_default="medium"),
        sa.Column("message", sa.Text, nullable=False),
        sa.Column("legal_basis", sa.Text, nullable=True),
        sa.Column("evidence_span", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("source", sa.String(10), nullable=False, server_default="rule"),
        sa.Column("status", sa.String(12), nullable=False, server_default="pending"),
        sa.Column("confirmed_by", UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_contract_findings_tenant_status", "contract_findings", ["tenant_id", "status"])


def downgrade() -> None:
    op.drop_table("contract_findings")
    op.drop_table("contract_clauses")
    op.drop_index("ix_contract_reviews_tenant_project", table_name="contract_reviews")
    op.drop_table("contract_reviews")
