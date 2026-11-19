"""p0 company assets schema

Revision ID: 0003_p0_assets
Revises: 0002_p0_opportunities
Create Date: 2026-10-21
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0003_p0_assets"
down_revision: str | None = "0002_p0_opportunities"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)

def upgrade() -> None:
    op.create_table(
        "companies",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("credit_code", sa.String(64), nullable=True),
        sa.Column("region", sa.String(200), nullable=True),
        sa.Column("industry", sa.String(120), nullable=True),
        sa.Column("contacts", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("source", sa.String(40), nullable=False, server_default="manual"),
        sa.Column("source_ref", sa.Text, nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("tenant_id", "name", name="uq_companies_tenant_name"),
    )
    op.create_index("ix_companies_tenant_id", "companies", ["tenant_id"])
    op.create_index("ix_companies_credit_code", "companies", ["credit_code"])
    op.create_table(
        "company_qualifications",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("company_id", UUID, sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("dedup_key", sa.String(64), nullable=False),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("level", sa.String(80), nullable=True),
        sa.Column("cert_no", sa.String(120), nullable=True),
        sa.Column("issuer", sa.String(200), nullable=True),
        sa.Column("valid_from", sa.Date, nullable=True),
        sa.Column("valid_to", sa.Date, nullable=True),
        sa.Column("source", sa.String(40), nullable=False, server_default="manual"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("tenant_id", "dedup_key", name="uq_qualifications_tenant_dedup"),
    )
    op.create_index("ix_company_qualifications_tenant_id", "company_qualifications", ["tenant_id"])
    op.create_index("ix_company_qualifications_company_id", "company_qualifications", ["company_id"])
    op.create_index("ix_company_qualifications_valid_to", "company_qualifications", ["valid_to"])
    op.create_table(
        "company_personnel",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("company_id", UUID, sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("dedup_key", sa.String(64), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("cert_type", sa.String(120), nullable=False),
        sa.Column("level", sa.String(80), nullable=True),
        sa.Column("major", sa.String(120), nullable=True),
        sa.Column("cert_no", sa.String(120), nullable=True),
        sa.Column("valid_to", sa.Date, nullable=True),
        sa.Column("source", sa.String(40), nullable=False, server_default="manual"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("tenant_id", "dedup_key", name="uq_personnel_tenant_dedup"),
    )
    op.create_index("ix_company_personnel_tenant_id", "company_personnel", ["tenant_id"])
    op.create_index("ix_company_personnel_company_id", "company_personnel", ["company_id"])
    op.create_index("ix_company_personnel_valid_to", "company_personnel", ["valid_to"])
    op.create_table(
        "company_performances",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("company_id", UUID, sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("dedup_key", sa.String(64), nullable=False),
        sa.Column("project_name", sa.String(300), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("complete_date", sa.Date, nullable=True),
        sa.Column("buyer", sa.String(300), nullable=True),
        sa.Column("category", sa.String(120), nullable=True),
        sa.Column("source", sa.String(40), nullable=False, server_default="manual"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("tenant_id", "dedup_key", name="uq_performances_tenant_dedup"),
    )
    op.create_index("ix_company_performances_tenant_id", "company_performances", ["tenant_id"])
    op.create_index("ix_company_performances_company_id", "company_performances", ["company_id"])
    op.create_table(
        "company_credits",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("company_id", UUID, sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("dedup_key", sa.String(64), nullable=False),
        sa.Column("check_item", sa.String(200), nullable=False),
        sa.Column("status", sa.String(80), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source", sa.String(40), nullable=False, server_default="manual"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("tenant_id", "dedup_key", name="uq_credits_tenant_dedup"),
    )
    op.create_index("ix_company_credits_tenant_id", "company_credits", ["tenant_id"])
    op.create_index("ix_company_credits_company_id", "company_credits", ["company_id"])


def downgrade() -> None:
    op.drop_table("company_credits")
    op.drop_table("company_performances")
    op.drop_table("company_personnel")
    op.drop_table("company_qualifications")
    op.drop_table("companies")
