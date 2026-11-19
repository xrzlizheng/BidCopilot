from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationAppError
from app.db.repository import TenantRepository
from app.modules.projects.models import BidResult
from app.modules.projects.schemas import BidResultUpsert
from app.modules.projects.service import (
    PROJECT_STATUS_LABELS,
    PROJECT_TRANSITIONS,
    get_project,
)

LOSS_REASON_LABELS = {
    "price": "报价",
    "technical": "技术方案",
    "qualification": "资格/资质",
    "compliance": "合规/格式",
    "relationship": "客户关系",
    "budget": "预算/流标",
    "other": "其他",
}


class BidResultRepository(TenantRepository[BidResult]):
    model = BidResult


async def record_result(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    project_id: UUID,
    user_id: UUID,
    body: BidResultUpsert,
) -> BidResult:
    project = await get_project(session, tenant_id, project_id)
    row = (
        await session.execute(
            select(BidResult).where(
                BidResult.tenant_id == tenant_id,
                BidResult.project_id == project_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = BidResult(tenant_id=tenant_id, project_id=project_id)
        session.add(row)
    row.outcome = body.outcome
    row.win_amount = body.win_amount if body.outcome == "won" else None
    row.loss_reason = body.loss_reason if body.outcome == "lost" else None
    row.competitor = body.competitor
    row.notes = body.notes
    row.recorded_by = user_id
    row.recorded_at = datetime.now(UTC)
    # 复盘结果也必须走状态机：won/lost/cancelled 只能从允许的当前状态进入，
    # 不能借 PUT /result 把项目写进状态机之外的状态。
    if body.outcome != project.status and body.outcome not in PROJECT_TRANSITIONS.get(
        project.status, set()
    ):
        raise ValidationAppError(
            f"不允许从「{PROJECT_STATUS_LABELS.get(project.status, project.status)}」"
            f"变更为「{PROJECT_STATUS_LABELS.get(body.outcome, body.outcome)}」"
        )
    project.status = body.outcome
    await session.commit()
    await session.refresh(row)
    return row


async def get_result(session: AsyncSession, tenant_id: UUID, project_id: UUID) -> BidResult | None:
    await get_project(session, tenant_id, project_id)
    return (
        await session.execute(
            select(BidResult).where(
                BidResult.tenant_id == tenant_id,
                BidResult.project_id == project_id,
            )
        )
    ).scalar_one_or_none()


async def bid_result_analytics(session: AsyncSession, tenant_id: UUID) -> dict[str, Any]:
    rows = list(
        (
            await session.execute(
                BidResultRepository(session, tenant_id)
                .scoped(select(BidResult))
                .order_by(BidResult.recorded_at)
            )
        )
        .scalars()
        .all()
    )
    won = sum(1 for row in rows if row.outcome == "won")
    lost = sum(1 for row in rows if row.outcome == "lost")
    cancelled = sum(1 for row in rows if row.outcome == "cancelled")
    decided = won + lost
    win_rate = round(won / decided * 100, 1) if decided else 0.0
    win_amount = sum(
        (row.win_amount for row in rows if row.outcome == "won" and row.win_amount is not None),
        Decimal("0"),
    )

    reasons: dict[str, int] = {}
    for row in rows:
        if row.outcome == "lost" and row.loss_reason:
            reasons[row.loss_reason] = reasons.get(row.loss_reason, 0) + 1

    months: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = row.recorded_at.strftime("%Y-%m") if row.recorded_at else "unknown"
        bucket = months.setdefault(
            key, {"month": key, "total": 0, "won": 0, "lost": 0, "cancelled": 0, "win_rate": 0.0}
        )
        bucket["total"] += 1
        bucket[row.outcome] += 1
    for bucket in months.values():
        month_decided = bucket["won"] + bucket["lost"]
        bucket["win_rate"] = round(bucket["won"] / month_decided * 100, 1) if month_decided else 0.0

    return {
        "summary": {
            "total": len(rows),
            "won": won,
            "lost": lost,
            "cancelled": cancelled,
            "win_rate": win_rate,
            "win_amount": str(win_amount),
        },
        "by_loss_reason": [
            {"reason": reason, "label": LOSS_REASON_LABELS.get(reason, reason), "count": count}
            for reason, count in sorted(reasons.items(), key=lambda item: item[1], reverse=True)
        ],
        "by_month": list(months.values())[-12:],
    }
