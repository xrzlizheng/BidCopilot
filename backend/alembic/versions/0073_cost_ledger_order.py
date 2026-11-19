"""cost ledger order uniqueness

同一网关 orderNo 只允许记一次账，防止重复回执抬高计费调用数。

升级前置要求（存量库）：若 `cost_ledger` 中已存在重复 `(tenant_id, order_no)`，
本迁移会在建索引前预检并**中止**，需人工清理重复账目后重试；迁移绝不自动
删除或修改财务账本数据。

Revision ID: 0073_cost_ledger_order
Revises: 0072_source_sync_status
Create Date: 2026-11-08
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0073_cost_ledger_order"
down_revision: str | None = "0072_source_sync_status"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 预检报告最多列出的重复组数，避免异常信息过长
_PREVIEW_LIMIT = 10


def _assert_no_duplicate_orders() -> None:
    """建唯一索引前预检重复 (tenant_id, order_no)。

    财务账本禁止自动清理：发现重复只报错中止，列出前若干组明细与处置指引，
    由人工核对 gateway 回执后决定保留/归档哪些行，再重跑迁移。
    """
    conn = op.get_bind()
    duplicates = conn.execute(
        sa.text(
            "SELECT tenant_id, order_no, COUNT(*) AS cnt, "
            "MIN(created_at) AS first_created_at "
            "FROM cost_ledger "
            "WHERE order_no IS NOT NULL "
            "GROUP BY tenant_id, order_no "
            "HAVING COUNT(*) > 1 "
            "ORDER BY cnt DESC, first_created_at ASC "
            "LIMIT :limit"
        ),
        {"limit": _PREVIEW_LIMIT},
    ).mappings().all()

    if not duplicates:
        return

    preview = "\n".join(
        f"  - tenant_id={row['tenant_id']} order_no={row['order_no']} "
        f"count={row['cnt']} first_created_at={row['first_created_at']}"
        for row in duplicates
    )
    raise RuntimeError(
        "cost_ledger 存在重复 (tenant_id, order_no) 账目，无法创建唯一索引 "
        "uq_cost_ledger_tenant_order（WHERE order_no IS NOT NULL）。\n"
        f"重复组（前 {len(duplicates)} 组）：\n{preview}\n"
        "处置指引：逐组核对网关 orderNo 回执，确认唯一入账记录（通常保留最早/charged=TRUE 的"
        "一行，其余行归档或标记作废），清理完成后重跑本迁移。"
        "本迁移不会自动删除或修改任何财务账本数据。"
    )


def upgrade() -> None:
    _assert_no_duplicate_orders()
    op.create_index(
        "uq_cost_ledger_tenant_order",
        "cost_ledger",
        ["tenant_id", "order_no"],
        unique=True,
        postgresql_where="order_no IS NOT NULL",
    )


def downgrade() -> None:
    raise NotImplementedError("forward-only migrations")
