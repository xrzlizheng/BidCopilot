"""grant tenant settings manage to tenant admin

Revision ID: 0050_tenant_settings_permission
Revises: 0049_bid_commitments
Create Date: 2026-10-27

`system/router.py` 的决策参数接口改为要求 `tenant:settings:manage`，而系统角色
的权限只在创建时写入（见 `app/cli.py`），已部署环境不会自动跟进。此迁移把该
能力前向补齐到企业管理员，避免升级后接口对全部角色返回 403。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0050_tenant_settings_permission"
down_revision: str | None = "0049_bid_commitments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CAPABILITY = "tenant:settings:manage"
ROLE_CODE = "tenant_admin"


def upgrade() -> None:
    op.get_bind().execute(
        sa.text(
            """
            UPDATE roles
               SET permissions = permissions || jsonb_build_array(CAST(:capability AS text))
             WHERE tenant_id IS NULL
               AND code = :role_code
               AND NOT (permissions @> jsonb_build_array(CAST(:capability AS text)))
            """
        ),
        {"capability": CAPABILITY, "role_code": ROLE_CODE},
    )


def downgrade() -> None:
    # 共享环境禁止 destructive downgrade；向前修复。
    pass
