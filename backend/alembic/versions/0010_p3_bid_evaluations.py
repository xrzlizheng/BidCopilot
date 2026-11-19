"""p3 ai bid evaluation

Revision ID: 0010_p3_bid_evaluations
Revises: 0009_p1_ocr_provenance
Create Date: 2026-10-21
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0010_p3_bid_evaluations"
down_revision: str | None = "0009_p1_ocr_provenance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "bid_evaluations",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", UUID, sa.ForeignKey("bid_projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="running"),
        sa.Column("model", sa.String(120), nullable=True),
        sa.Column("prompt_version", sa.String(40), nullable=False, server_default="v1"),
        sa.Column("result_json", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("created_by", UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_bid_evaluations_tenant_id", "bid_evaluations", ["tenant_id"])
    op.create_index(
        "ix_bid_evaluations_tenant_project", "bid_evaluations", ["tenant_id", "project_id"]
    )


def downgrade() -> None:
    op.drop_table("bid_evaluations")
