"""p2c department assets

Revision ID: 0034_p2c_department_assets
Revises: 0033_p2b_break_glass
Create Date: 2026-10-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0034_p2c_department_assets"
down_revision: str | None = "0033_p2b_break_glass"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)

TABLES = {
    "company_qualifications": "ix_qualifications_tenant_org",
    "company_personnel": "ix_personnel_tenant_org",
    "company_performances": "ix_performances_tenant_org",
    "company_credits": "ix_credits_tenant_org",
}


def upgrade() -> None:
    for table, index_name in TABLES.items():
        op.add_column(table, sa.Column("org_unit_id", UUID, nullable=True))
        op.create_foreign_key(
            f"fk_{table}_org_unit",
            table,
            "org_units",
            ["org_unit_id"],
            ["id"],
            ondelete="SET NULL",
        )
        op.create_index(index_name, table, ["tenant_id", "org_unit_id"])


def downgrade() -> None:
    # 共享环境禁止 destructive downgrade；向前修复。
    pass
