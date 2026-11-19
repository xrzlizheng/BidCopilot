"""contract lifecycle (CLM lite): records / drafts / approvals / ledger / changes

Revision ID: 0054_contract_lifecycle
Revises: 0053_opportunity_matches
Create Date: 2026-10-28

既有 contract_reviews / contract_clauses / contract_findings / contract_milestones /
tenant_contract_rules 保持不变；合同主数据通过 contract_records.review_id 关联审查记录。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0054_contract_lifecycle"
down_revision: str | None = "0053_opportunity_matches"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB


def upgrade() -> None:
    op.create_table(
        "contract_records",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "tenant_id", UUID, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "project_id",
            UUID,
            sa.ForeignKey("bid_projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "review_id",
            UUID,
            sa.ForeignKey("contract_reviews.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("contract_no", sa.String(80), nullable=False),
        sa.Column("title", sa.String(400), nullable=False),
        sa.Column("party_a", sa.String(300), nullable=False, server_default=""),
        sa.Column("party_b", sa.String(300), nullable=False, server_default=""),
        sa.Column("signed_date", sa.Date(), nullable=True),
        sa.Column("effective_date", sa.Date(), nullable=True),
        sa.Column("expiry_date", sa.Date(), nullable=True),
        sa.Column("amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("currency", sa.String(8), nullable=False, server_default="CNY"),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column(
            "owner_id", UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column(
            "created_by", UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("draft_generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("draft_disclaimer", sa.Text(), nullable=True),
        sa.Column("expiry_reminded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("tenant_id", "contract_no", name="uq_contract_records_no"),
    )
    op.create_index(
        "ix_contract_records_tenant_status", "contract_records", ["tenant_id", "status"]
    )
    op.create_index(
        "ix_contract_records_tenant_project", "contract_records", ["tenant_id", "project_id"]
    )
    op.create_index(
        "ix_contract_records_expiry", "contract_records", ["tenant_id", "status", "expiry_date"]
    )

    op.create_table(
        "contract_draft_clauses",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "tenant_id", UUID, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "record_id",
            UUID,
            sa.ForeignKey("contract_records.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("category", sa.String(40), nullable=False, server_default="other"),
        sa.Column("source_json", JSONB, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_contract_draft_clauses_record",
        "contract_draft_clauses",
        ["record_id", "seq"],
    )

    op.create_table(
        "contract_approvals",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "tenant_id", UUID, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "record_id",
            UUID,
            sa.ForeignKey("contract_records.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column(
            "actor_id", UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("from_status", sa.String(20), nullable=True),
        sa.Column("to_status", sa.String(20), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_contract_approvals_record", "contract_approvals", ["record_id", "created_at"]
    )

    op.create_table(
        "contract_ledger_entries",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "tenant_id", UUID, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "record_id",
            UUID,
            sa.ForeignKey("contract_records.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("entry_type", sa.String(20), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("occurred_on", sa.Date(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("voucher_ref", sa.String(300), nullable=True),
        sa.Column(
            "created_by", UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_contract_ledger_entries_record",
        "contract_ledger_entries",
        ["record_id", "occurred_on"],
    )

    op.create_table(
        "contract_changes",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "tenant_id", UUID, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "record_id",
            UUID,
            sa.ForeignKey("contract_records.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("change_type", sa.String(20), nullable=False),
        sa.Column("amount_before", sa.Numeric(18, 2), nullable=True),
        sa.Column("amount_after", sa.Numeric(18, 2), nullable=True),
        sa.Column("expiry_before", sa.Date(), nullable=True),
        sa.Column("expiry_after", sa.Date(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "approval_status", sa.String(20), nullable=False, server_default="pending"
        ),
        sa.Column(
            "requested_by", UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column(
            "approved_by", UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_contract_changes_record", "contract_changes", ["record_id", "created_at"])


def downgrade() -> None:
    # 迁移只前进；共享环境禁止 destructive downgrade。
    pass
