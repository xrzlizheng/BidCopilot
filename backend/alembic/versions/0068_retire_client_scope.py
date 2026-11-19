"""retire multi-client (agency) capability

产品定位确认只面向企业投标部门（单一投标主体），不存在「代理机构代多家客户投标」
的场景。因此整体下线多客户能力：

- 删除 `clients` / `client_members` 两张表
- 删除 companies / bid_projects / knowledge_bases / knowledge_packs /
  ai_usage_ledger / intel_watchlist 上的 `client_id` 列

只前进：不提供 downgrade 恢复已删除的业务数据（本迁移执行前两库 clients 均为 0 行）。
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0068_retire_client_scope"
down_revision: str | None = "0067_webhooks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 先删索引 → 再删外键 → 最后删列，避免悬空依赖。
CLIENT_INDEXES: tuple[str, ...] = (
    "ix_ai_usage_tenant_client",
    "ix_companies_client_id",
    "ix_bid_projects_client_id",
    "ix_knowledge_bases_client_id",
    "ix_knowledge_packs_client_id",
)

# 各表实际存在的外键名（以数据库实证为准，不按模型默认命名推测）。
CLIENT_FOREIGN_KEYS: tuple[tuple[str, str], ...] = (
    ("companies", "fk_companies_client_id"),
    ("bid_projects", "fk_bid_projects_client_id"),
    ("knowledge_bases", "fk_knowledge_bases_client_id"),
    ("knowledge_packs", "fk_knowledge_packs_client_id"),
    ("ai_usage_ledger", "ai_usage_ledger_client_id_fkey"),
    ("intel_watchlist", "intel_watchlist_client_id_fkey"),
)

CLIENT_COLUMN_TABLES: tuple[str, ...] = tuple(
    table for table, _ in CLIENT_FOREIGN_KEYS
)


def upgrade() -> None:
    for index_name in CLIENT_INDEXES:
        op.drop_index(index_name, table_name=None, if_exists=True)

    for table, fk_name in CLIENT_FOREIGN_KEYS:
        op.drop_constraint(fk_name, table, type_="foreignkey")

    for table in CLIENT_COLUMN_TABLES:
        op.drop_column(table, "client_id")

    # 成员关系先于主表删除。
    op.drop_table("client_members")
    op.drop_table("clients")


def downgrade() -> None:
    raise NotImplementedError(
        "0068 已删除多客户能力与相关数据，按项目规范（迁移只前进、禁止 destructive downgrade）不提供回滚。"
    )
