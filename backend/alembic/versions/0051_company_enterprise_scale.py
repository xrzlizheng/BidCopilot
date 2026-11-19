"""add enterprise scale fields to companies

Revision ID: 0051_company_enterprise_scale
Revises: 0050_tenant_settings_permission
Create Date: 2026-10-28

政府采购「专门面向中小企业」的项目，声明函填错即废标（且可能被认定为提供虚假
材料）。要自动判断企业能否投这类项目，必须先把划型所需的原始指标落到公司主数据：

- `employee_count`：从业人员数
- `annual_revenue`：营业收入（万元）
- `total_assets`：资产总额（万元）
- `enterprise_industry`：划型用的标准行业 key（与营业执照行业口径不完全一致）
- `enterprise_scale`：已确认的划型结果，允许人工覆盖自动计算值

单位统一用万元，与《中小企业划型标准规定》一致，避免每次换算引入误差。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0051_company_enterprise_scale"
down_revision: str | None = "0050_tenant_settings_permission"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("companies", sa.Column("employee_count", sa.Integer(), nullable=True))
    op.add_column("companies", sa.Column("annual_revenue", sa.Numeric(18, 2), nullable=True))
    op.add_column("companies", sa.Column("total_assets", sa.Numeric(18, 2), nullable=True))
    op.add_column("companies", sa.Column("enterprise_industry", sa.String(40), nullable=True))
    op.add_column("companies", sa.Column("enterprise_scale", sa.String(10), nullable=True))
    op.add_column(
        "companies",
        sa.Column(
            "enterprise_scale_source",
            sa.String(20),
            nullable=False,
            server_default="auto",
        ),
    )
    op.add_column(
        "companies",
        sa.Column("enterprise_scale_checked_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    for column in (
        "enterprise_scale_checked_at",
        "enterprise_scale_source",
        "enterprise_scale",
        "enterprise_industry",
        "total_assets",
        "annual_revenue",
        "employee_count",
    ):
        op.drop_column("companies", column)
