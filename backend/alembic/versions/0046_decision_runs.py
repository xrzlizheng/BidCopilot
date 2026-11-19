"""bid decision card runs

Revision ID: 0046_decision_runs
Revises: 0045_section_ai_meta
Create Date: 2026-10-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID

from alembic import op

revision: str = "0046_decision_runs"
down_revision: str | None = "0045_section_ai_meta"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "bid_decision_runs",
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
        sa.Column("run_no", sa.Integer(), nullable=False),
        sa.Column("engine_mode", sa.String(20), nullable=False, server_default="full"),
        sa.Column("engine_version", sa.String(40), nullable=False, server_default="decision-v1"),
        sa.Column(
            "constraint_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("tender_constraints.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("constraint_version", sa.Integer(), nullable=True),
        sa.Column(
            "company_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("profile_snapshot", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("inputs_json", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("dimensions_json", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("vetoes_json", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("score", sa.Numeric(6, 2), nullable=True),
        sa.Column("computed_status", sa.String(30), nullable=False),
        sa.Column("human_status", sa.String(30), nullable=True),
        sa.Column("thresholds_json", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("source_labels", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "created_by",
            PGUUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "confirmed_by",
            PGUUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("override_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("project_id", "run_no", name="uq_bid_decision_run_no"),
    )
    op.create_index(
        "ix_bid_decision_runs_tenant_project",
        "bid_decision_runs",
        ["tenant_id", "project_id", "created_at"],
    )


def downgrade() -> None:
    # 共享环境禁止 destructive downgrade；向前修复。
    pass
