import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import SessionLocal
from app.modules.costs.models import CostLedger
from app.modules.opportunities.models import SourceSyncRun

FAILED_STATUSES = {"failed"}


def query_hash(value: Any) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


async def record_gateway_call(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    pcode: str,
    operation: str,
    status: str,
    order_no: str | None,
    charged: bool,
    rows: int,
    query_hash_value: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    created_by: UUID | None = None,
) -> CostLedger:
    entry = CostLedger(
        tenant_id=tenant_id,
        pcode=pcode,
        operation=operation,
        status=status,
        order_no=order_no,
        charged=charged,
        rows=rows,
        query_hash=query_hash_value,
        resource_type=resource_type,
        resource_id=resource_id,
        created_by=created_by,
    )
    # 独立 session 记账：orderNo 重复时不污染调用方的业务事务。
    async with SessionLocal() as ledger_session:
        ledger_session.add(entry)
        try:
            await ledger_session.commit()
        except IntegrityError:
            await ledger_session.rollback()
            existing = (
                await ledger_session.execute(
                    select(CostLedger).where(
                        CostLedger.tenant_id == tenant_id,
                        CostLedger.order_no == order_no,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                return existing
            raise
        await ledger_session.refresh(entry)
    return entry


def _failed(status: str) -> bool:
    return status in FAILED_STATUSES or status not in {"ok", "empty", "succeeded"}


async def data_ops_summary(session: AsyncSession, tenant_id: UUID, days: int) -> dict[str, Any]:
    since = datetime.now(UTC) - timedelta(days=days)
    cost_rows = list(
        (
            await session.execute(
                select(CostLedger)
                .where(CostLedger.tenant_id == tenant_id, CostLedger.created_at >= since)
                .order_by(CostLedger.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    sync_rows = list(
        (
            await session.execute(
                select(SourceSyncRun)
                .where(SourceSyncRun.tenant_id == tenant_id, SourceSyncRun.created_at >= since)
                .order_by(SourceSyncRun.created_at.desc())
            )
        )
        .scalars()
        .all()
    )

    by_pcode: dict[str, dict[str, Any]] = {}
    by_day: dict[str, dict[str, Any]] = {}
    for row in cost_rows:
        bucket = by_pcode.setdefault(
            row.pcode,
            {"pcode": row.pcode, "calls": 0, "charged_calls": 0, "failed_calls": 0, "rows": 0},
        )
        bucket["calls"] += 1
        bucket["charged_calls"] += 1 if row.charged else 0
        bucket["failed_calls"] += 1 if _failed(row.status) else 0
        bucket["rows"] += row.rows
        day = row.created_at.strftime("%Y-%m-%d")
        day_bucket = by_day.setdefault(day, {"date": day, "calls": 0, "charged_calls": 0})
        day_bucket["calls"] += 1
        day_bucket["charged_calls"] += 1 if row.charged else 0

    sources: dict[tuple[str, str], dict[str, Any]] = {}
    for run in sync_rows:
        key = (run.source, run.pcode)
        bucket = sources.setdefault(
            key,
            {
                "source": run.source,
                "pcode": run.pcode,
                "runs": 0,
                "succeeded": 0,
                "failed": 0,
                "last_status": None,
                "last_error": None,
                "last_failed_error": None,
                "last_failed_at": None,
                "last_run_at": None,
            },
        )
        bucket["runs"] += 1
        if run.status == "succeeded":
            bucket["succeeded"] += 1
        else:
            bucket["failed"] += 1
            if bucket["last_failed_at"] is None:
                bucket["last_failed_at"] = run.created_at
                bucket["last_failed_error"] = run.error_message
        if bucket["last_run_at"] is None:
            bucket["last_run_at"] = run.created_at
            bucket["last_status"] = run.status
            bucket["last_error"] = run.error_message
    source_list = []
    for bucket in sources.values():
        bucket["success_rate"] = (
            round(bucket["succeeded"] / bucket["runs"] * 100, 1) if bucket["runs"] else 0.0
        )
        source_list.append(bucket)

    return {
        "period_days": days,
        "costs": {
            "total_calls": len(cost_rows),
            "charged_calls": sum(1 for row in cost_rows if row.charged),
            "failed_calls": sum(1 for row in cost_rows if _failed(row.status)),
            "rows": sum(row.rows for row in cost_rows),
            "by_pcode": sorted(by_pcode.values(), key=lambda item: item["calls"], reverse=True),
            "by_day": sorted(by_day.values(), key=lambda item: item["date"]),
            "recent": [
                {
                    "pcode": row.pcode,
                    "operation": row.operation,
                    "status": row.status,
                    "order_no": row.order_no,
                    "charged": row.charged,
                    "rows": row.rows,
                    "created_at": row.created_at,
                }
                for row in cost_rows[:10]
            ],
        },
        "sources": sorted(source_list, key=lambda item: item["source"]),
    }
