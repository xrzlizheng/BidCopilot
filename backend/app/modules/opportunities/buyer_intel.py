import unicodedata
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repository import TenantRepository
from app.modules.assets.models import Company
from app.modules.assets.repository import CompanyRepository
from app.modules.costs.quota import release_paid_call, reserve_paid_call
from app.modules.gateway.company_adapter import KeepRiskCompanyIntelAdapter
from app.modules.opportunities.models import Opportunity, OpportunityBuyerIntel
from app.modules.opportunities.repository import BuyerIntelRepository
from app.modules.projects.models import BidResult

LOOKBACK_DAYS = 365
EXISTING_SUPPLIER_MIN_WINS = 2


class _BidResultLookup(TenantRepository[BidResult]):
    model = BidResult


def _winner_key(winner: str) -> str:
    """NFKC + casefold + 空白归一：同一厂商的全半角/大小写/空格差异合并。"""
    return " ".join(unicodedata.normalize("NFKC", winner).casefold().split())


async def _tracked_names(session: AsyncSession, tenant_id: UUID) -> set[str]:
    rows = (
        await session.execute(
            CompanyRepository(session, tenant_id).scoped(select(Company.name))
        )
    ).scalars().all()
    return {_winner_key(str(row)) for row in rows if row and str(row).strip()}


async def _noted_competitors(session: AsyncSession, tenant_id: UUID) -> set[str]:
    """复盘里人工填过的竞争对手文本，用于标记"我们和这家交过手"。"""
    rows = (
        await session.execute(
            _BidResultLookup(session, tenant_id)
            .scoped(select(BidResult.competitor))
            .where(BidResult.competitor.is_not(None))
        )
    ).scalars().all()
    return {_winner_key(str(row)) for row in rows if row and str(row).strip()}


def _aggregate(
    items: list[Any],
    *,
    tracked: set[str],
    noted: set[str],
    cutoff: datetime,
) -> dict[str, Any]:
    winner_rows: list[dict[str, Any]] = []
    tally: dict[str, dict[str, Any]] = {}
    recent_wins = 0
    for item in items:
        display_name = (item.winner or "").strip()
        key = _winner_key(display_name)
        publish_at = item.publish_at
        recent = publish_at is not None and publish_at >= cutoff
        if recent:
            recent_wins += 1
        winner_rows.append(
            {
                "winner": display_name,
                "title": item.title,
                "publish_at": publish_at.isoformat() if publish_at else None,
                "win_bid_amount": str(item.win_bid_amount) if item.win_bid_amount is not None else None,
                "source_url": item.source_url,
                "recent": recent,
                "tracked": key in tracked,
                "competitor_noted": key in noted,
            }
        )
        bucket = tally.setdefault(
            key,
            {
                "winner": display_name,
                "wins": 0,
                "recent_wins": 0,
                "latest_at": None,
                "total_amount": None,
            },
        )
        bucket["wins"] += 1
        if recent:
            bucket["recent_wins"] += 1
        if publish_at is not None and (
            bucket["latest_at"] is None or publish_at.isoformat() > bucket["latest_at"]
        ):
            bucket["latest_at"] = publish_at.isoformat()
        if item.win_bid_amount is not None:
            previous: Decimal = bucket["total_amount"] or Decimal("0")
            bucket["total_amount"] = previous + item.win_bid_amount
    top_winners = sorted(
        (
            {
                "winner": bucket["winner"],
                "wins": int(bucket["wins"]),
                "recent_wins_12m": int(bucket["recent_wins"]),
                "latest_at": bucket["latest_at"],
                "total_amount": (
                    f"{bucket['total_amount']:.2f}" if bucket["total_amount"] is not None else None
                ),
                "tracked": key in tracked,
                "competitor_noted": key in noted,
            }
            for key, bucket in tally.items()
        ),
        key=lambda row: (-row["wins"], row["winner"]),
    )
    # 文档口径：近 12 个月内同一厂商中标 ≥2 次才是既有供应商；不能把更早的历史累加进来。
    existing = [
        row for row in top_winners if row["recent_wins_12m"] >= EXISTING_SUPPLIER_MIN_WINS
    ]
    return {
        "winner_rows": winner_rows,
        "top_winners": top_winners,
        "existing_suppliers": existing,
        "recent_wins_12m": recent_wins,
    }


async def sync_buyer_intel(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    user_id: UUID,
    opportunity: Opportunity,
    adapter: KeepRiskCompanyIntelAdapter,
) -> OpportunityBuyerIntel:
    buyer = (opportunity.buyer or "").strip()
    reservation = await reserve_paid_call(tenant_id)
    try:
        result = await adapter.fetch_bids(
            buyer, role="tenderer", page_size=50, info_types=["3"]
        )
    except Exception:
        await release_paid_call(reservation)
        raise
    if not result.charged:
        await release_paid_call(reservation)
    now = datetime.now(UTC)
    values: dict[str, Any] = {
        "buyer_name": buyer,
        # DataResult.status 原样落库：ok/empty/unauthorized/rate_limited... 不再二值化。
        "status": result.status.value,
        "created_by": user_id,
    }
    if result.is_success:
        awards = [
            item
            for item in result.data or []
            if item.info_type == "award" and (item.winner or "").strip()
        ]
        tracked = await _tracked_names(session, tenant_id)
        noted = await _noted_competitors(session, tenant_id)
        values.update(
            _aggregate(
                awards,
                tracked=tracked,
                noted=noted,
                cutoff=now - timedelta(days=LOOKBACK_DAYS),
            )
        )
        values.update(
            {
                "rows_fetched": len(result.data or []),
                "snapshot_ref": result.raw_ref,
                "gateway_order_no": result.order_no,
                "charged": result.charged,
                "synced_at": now,
                "error_code": None,
                "error_message": None,
            }
        )
    else:
        # 失败只写状态与错误，保留上次成功的聚合与溯源，避免瞬时失败清空榜单。
        error = result.error
        values["error_code"] = error.code if error else result.status.value
        values["error_message"] = error.message if error else result.status.value

    statement = (
        insert(OpportunityBuyerIntel)
        .values(
            tenant_id=tenant_id,
            opportunity_id=opportunity.id,
            **values,
        )
        .on_conflict_do_update(
            index_elements=["tenant_id", "opportunity_id"],
            set_={key: value for key, value in values.items() if key != "created_by"},
        )
        .returning(OpportunityBuyerIntel.id)
    )
    row_id = (await session.execute(statement)).scalar_one()
    await session.commit()
    row = await session.get(OpportunityBuyerIntel, row_id)
    assert row is not None
    return row


async def get_buyer_intel(
    session: AsyncSession, *, tenant_id: UUID, opportunity_id: UUID
) -> OpportunityBuyerIntel | None:
    return (
        await session.execute(
            BuyerIntelRepository(session, tenant_id)
            .scoped(select(OpportunityBuyerIntel))
            .where(OpportunityBuyerIntel.opportunity_id == opportunity_id)
        )
    ).scalars().first()
