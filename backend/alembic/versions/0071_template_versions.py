"""contract template versions

合同审查 backlog 项：审查点模板版本快照。租户模板每次 PATCH/revert 前把当前
内容整体快照进本表（version 递增），支持回溯；系统模板不可变，不产生版本。

Revision ID: 0071_template_versions
Revises: 0070_law_articles
Create Date: 2026-11-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0071_template_versions"
down_revision: str | None = "0070_law_articles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "contract_template_versions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "template_id",
            UUID,
            sa.ForeignKey("contract_review_templates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        # 快照 = 模板全部可编辑字段的整体 JSON（回退即按快照覆盖）。
        sa.Column("snapshot", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_by", UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "ix_contract_template_versions_template", "contract_template_versions", ["template_id"]
    )
    op.create_unique_constraint(
        "uq_contract_template_versions_template_version",
        "contract_template_versions",
        ["template_id", "version"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_contract_template_versions_template_version",
        "contract_template_versions",
        type_="unique",
    )
    op.drop_index("ix_contract_template_versions_template", table_name="contract_template_versions")
    op.drop_table("contract_template_versions")
