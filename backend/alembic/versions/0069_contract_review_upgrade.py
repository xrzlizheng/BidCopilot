"""contract review upgrade: review-point templates, finding confidence/redline

合同审查升级（吸收开源合同审查项目亮点，详见 docs/ 与调研记录）：

- 新表 `contract_review_templates`：审查点模板（关键词/审查点/审查目的/审查纪律）。
  is_system=True 为平台级只读目录（tenant_id 为空），随本迁移种子化 25 套模板
  （转换自 xiaodingfeng/contract-review，MIT）；租户模板行为后续能力预留。
- `contract_findings` 加列：confidence / detection_method / redline（全部可空，
  历史行不回填——旧数据按"未知来源"呈现）。
- `contract_reviews` 加列：replaces_review_id（修订对照链路）、template_matches
  （模板匹配与应用结果快照）。

Revision ID: 0069_contract_review_upgrade
Revises: 0068_retire_client_scope
Create Date: 2026-11-07
"""

from collections.abc import Sequence
from json import dumps
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.modules.contracts.data.review_templates import SYSTEM_TEMPLATES

revision: str = "0069_contract_review_upgrade"
down_revision: str | None = "0068_retire_client_scope"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "contract_review_templates",
        sa.Column("id", UUID, primary_key=True),
        # 系统目录行 tenant_id 为空（平台级共享，不挂租户外键语义）；
        # 租户级行指向租户。查询用 or_(is_system, tenant_id=当前租户) 放行。
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("contract_type", sa.String(60), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("contract_type_keywords", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("review_points", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("core_purposes", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("prompt_rules", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("report_sections", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("typical_description", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_contract_review_templates_tenant_id", "contract_review_templates", ["tenant_id"])
    # 系统模板按 contract_type 全局唯一；租户模板按 (tenant_id, contract_type) 唯一。
    op.create_index(
        "ux_contract_review_templates_system",
        "contract_review_templates",
        ["contract_type"],
        unique=True,
        postgresql_where=sa.text("is_system"),
    )
    op.create_index(
        "ux_contract_review_templates_tenant",
        "contract_review_templates",
        ["tenant_id", "contract_type"],
        unique=True,
        postgresql_where=sa.text("NOT is_system"),
    )

    op.add_column(
        "contract_findings",
        sa.Column("confidence", sa.Numeric(3, 2), nullable=True),
    )
    op.add_column(
        "contract_findings",
        sa.Column("detection_method", sa.String(16), nullable=True),
    )
    op.add_column("contract_findings", sa.Column("redline", sa.Text(), nullable=True))

    op.add_column(
        "contract_reviews",
        sa.Column(
            "replaces_review_id",
            UUID,
            sa.ForeignKey("contract_reviews.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "contract_reviews",
        sa.Column("template_matches", postgresql.JSONB(), nullable=True),
    )

    _seed_system_templates()


def _seed_system_templates() -> None:
    """幂等种子：按 contract_type 跳过已存在系统模板（重跑/已手工建过不覆盖）。"""
    bind = op.get_bind()
    existing = {
        row[0]
        for row in bind.execute(
            sa.text("SELECT contract_type FROM contract_review_templates WHERE is_system")
        )
    }
    insert_sql = sa.text(
        """
        INSERT INTO contract_review_templates (
            id, tenant_id, is_system, contract_type, name,
            contract_type_keywords, review_points, core_purposes,
            prompt_rules, report_sections, typical_description, enabled
        ) VALUES (
            :id, NULL, TRUE, :contract_type, :name,
            CAST(:contract_type_keywords AS jsonb), CAST(:review_points AS jsonb), CAST(:core_purposes AS jsonb),
            CAST(:prompt_rules AS jsonb), CAST(:report_sections AS jsonb), :typical_description, TRUE
        )
        """
    )
    for template in SYSTEM_TEMPLATES:
        if template["contract_type"] in existing:
            continue
        bind.execute(
            insert_sql,
            {
                "id": str(uuid4()),
                "contract_type": template["contract_type"],
                "name": template["name"],
                "contract_type_keywords": dumps(
                    template["contract_type_keywords"], ensure_ascii=False
                ),
                "review_points": dumps(template["review_points"], ensure_ascii=False),
                "core_purposes": dumps(template["core_purposes"], ensure_ascii=False),
                "prompt_rules": dumps(template["prompt_rules"], ensure_ascii=False),
                "report_sections": dumps(template["report_sections"], ensure_ascii=False),
                "typical_description": template.get("typical_description"),
            },
        )


def downgrade() -> None:
    op.drop_column("contract_reviews", "template_matches")
    op.drop_column("contract_reviews", "replaces_review_id")
    op.drop_column("contract_findings", "redline")
    op.drop_column("contract_findings", "detection_method")
    op.drop_column("contract_findings", "confidence")
    op.drop_index("ux_contract_review_templates_tenant", table_name="contract_review_templates")
    op.drop_index("ux_contract_review_templates_system", table_name="contract_review_templates")
    op.drop_index("ix_contract_review_templates_tenant_id", table_name="contract_review_templates")
    op.drop_table("contract_review_templates")
