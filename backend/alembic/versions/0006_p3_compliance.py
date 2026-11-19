"""p3 compliance schema

Revision ID: 0006_p3_compliance
Revises: 0005_p2_generation
Create Date: 2026-10-21
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0006_p3_compliance"
down_revision: str | None = "0005_p2_generation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "compliance_reports",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", UUID, sa.ForeignKey("bid_projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="completed"),
        sa.Column("rule_version", sa.String(40), nullable=False),
        sa.Column("file_hash", sa.String(64), nullable=False),
        sa.Column("summary_json", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_by", UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_compliance_reports_tenant_id", "compliance_reports", ["tenant_id"])
    op.create_index("ix_compliance_reports_tenant_project", "compliance_reports", ["tenant_id", "project_id"])
    op.create_table(
        "findings",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", UUID, sa.ForeignKey("bid_projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("report_id", UUID, sa.ForeignKey("compliance_reports.id", ondelete="CASCADE"), nullable=False),
        sa.Column("rule_id", sa.String(80), nullable=False),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("finding_type", sa.String(20), nullable=False),
        sa.Column("message", sa.Text, nullable=False),
        sa.Column("location", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("expected", sa.Text, nullable=True),
        sa.Column("actual", sa.Text, nullable=True),
        sa.Column("basis", sa.Text, nullable=True),
        sa.Column("suggestion", sa.Text, nullable=True),
        sa.Column("auto_fixable", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("needs_review", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column("evidence_ref", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_findings_tenant_id", "findings", ["tenant_id"])
    op.create_index("ix_findings_tenant_report", "findings", ["tenant_id", "report_id"])


def downgrade() -> None:
    op.drop_table("findings")
    op.drop_table("compliance_reports")
