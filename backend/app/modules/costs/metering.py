"""AI 产出侧计量：按项目 / 按客户记录篇数与字数。

与 `costs/quota.py`（外部网关按次计费）分工不同：
- `cost_ledger` / `paid_call_quotas`：外部付费调用的成本与配额；
- `ai_usage_ledger`：本系统 AI 产出了多少篇、多少字，供代理机构按客户分摊与套餐校验。

约束：`status` 原样记录，失败不折算为 0 消耗；查询必须按租户隔离。
"""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.costs.metering_models import AiUsageLedger


def count_words(text: str) -> int:
    """中英混排字数口径：CJK 按字计数，其余按空白分词计数。"""
    if not text:
        return 0
    cjk = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
    rest = "".join(" " if "\u4e00" <= char <= "\u9fff" else char for char in text)
    words = len([token for token in rest.split() if token])
    return cjk + words


async def record_ai_usage(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    project_id: UUID,
    operation: str,
    status: str = "ok",
    piece_count: int = 0,
    word_count: int = 0,
    model: str | None = None,
    created_by: UUID | None = None,
) -> AiUsageLedger:
    entry = AiUsageLedger(
        tenant_id=tenant_id,
        project_id=project_id,
        operation=operation,
        status=status,
        piece_count=piece_count,
        word_count=word_count,
        model=model,
        created_by=created_by,
    )
    session.add(entry)
    await session.commit()
    await session.refresh(entry)
    return entry


async def usage_by_project(
    session: AsyncSession, tenant_id: UUID, project_id: UUID
) -> dict[str, int]:
    row = (
        await session.execute(
            select(
                func.coalesce(func.sum(AiUsageLedger.piece_count), 0),
                func.coalesce(func.sum(AiUsageLedger.word_count), 0),
            ).where(
                AiUsageLedger.tenant_id == tenant_id,
                AiUsageLedger.project_id == project_id,
            )
        )
    ).one()
    return {"pieces": int(row[0]), "words": int(row[1])}

