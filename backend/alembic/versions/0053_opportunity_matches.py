"""persist opportunity matches and auto-screening decisions

Revision ID: 0053_opportunity_matches
Revises: 0052_pricing_strategy
Create Date: 2026-11-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID

from alembic import op

revision: str = "0053_opportunity_matches"
down_revision: str | None = "0052_pricing_strategy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "opportunity_matches",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "opportunity_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("opportunities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("match_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "hit_groups_json", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column(
            "reasons_json", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column(
            "risks_json", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column("qualified", sa.Boolean(), nullable=True),
        sa.Column(
            "qualification_gaps_json",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("decision", sa.String(20), nullable=False, server_default="review"),
        sa.Column(
            "engine_version", sa.String(40), nullable=False, server_default="heuristic-rules-v1"
        ),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "project_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("bid_projects.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "tenant_id", "opportunity_id", name="uq_opportunity_matches_tenant_opportunity"
        ),
    )
    op.create_index(
        "ix_opportunity_matches_tenant_decision",
        "opportunity_matches",
        ["tenant_id", "decision"],
    )
    op.create_index(
        "ix_opportunity_matches_tenant_computed",
        "opportunity_matches",
        ["tenant_id", "computed_at"],
    )


def downgrade() -> None:
    # 共享环境禁止 destructive downgrade；向前修复。
    pass
