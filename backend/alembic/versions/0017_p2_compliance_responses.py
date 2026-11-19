"""p2 item-by-item compliance response matrix

Revision ID: 0017_p2_compliance_responses
Revises: 0016_p2_knowledge_base
Create Date: 2026-10-23
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0017_p2_compliance_responses"
down_revision: str | None = "0016_p2_knowledge_base"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "compliance_responses",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "project_id",
            UUID,
            sa.ForeignKey("bid_projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "constraint_id",
            UUID,
            sa.ForeignKey("tender_constraints.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("requirement_type", sa.String(30), nullable=False),
        sa.Column("requirement_key", sa.String(64), nullable=False),
        sa.Column("requirement_text", sa.Text(), nullable=False),
        sa.Column("category", sa.String(40), nullable=False, server_default="其他"),
        sa.Column("source_span", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("response_status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("response_note", sa.Text(), nullable=True),
        sa.Column("evidence_refs", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("section_ref", sa.String(100), nullable=True),
        sa.Column("confirmed_by", UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("constraint_id", "requirement_key", name="uq_compliance_responses_key"),
    )
    op.create_index(
        "ix_compliance_responses_tenant_project",
        "compliance_responses",
        ["tenant_id", "project_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_compliance_responses_tenant_project", table_name="compliance_responses")
    op.drop_table("compliance_responses")
