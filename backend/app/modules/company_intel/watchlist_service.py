import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.errors import ConflictError, NotFoundError, ValidationAppError
from app.db.session import SessionLocal
from app.modules.company_intel.watchlist_models import DEFAULT_WATCH_SECTIONS, IntelWatchlist
from app.modules.costs.quota import QuotaReservation, release_paid_call, reserve_paid_call
from app.modules.costs.service import query_hash, record_gateway_call
from app.modules.gateway.contract import DataResult
from app.modules.gateway.enterprise_adapter import EnterpriseIntelAdapter
from app.modules.identity.schemas import CurrentUser
from app.modules.notifications.service import NotificationService

logger = logging.getLogger(__name__)

TOOL = "enterprise_due_diligence_snapshot"


def _digest(payload: dict[str, Any]) -> dict[str, Any]:
    sections = payload.get("sections") or []
    digest: dict[str, Any] = {}
    has_records = False
    for section in sections:
        if not isinstance(section, dict):
            continue
        item = section.get("digest") or {}
        if item.get("has_records"):
            has_records = True
        digest[str(section.get("key"))] = {
            "risk": item.get("risk_level"),
            "count": item.get("count"),
            "status": section.get("status"),
            "summary": item.get("summary"),
        }
    overall = (payload.get("risk_summary") or {}).get("overall_level")
    # 无任何记录时上游仍可能给 green；没有主体存在性证据时不冒充安全。
    digest["_risk"] = overall if has_records else "unknown"
    return digest


def _changes(old: dict[str, Any] | None, new: dict[str, Any]) -> str | None:
    if not old:
        return None
    diffs: list[str] = []
    for key, value in new.items():
        previous = old.get(key)
        if previous == value:
            continue
        if key == "_risk":
            diffs.append(f"总体风险 {previous or '—'} → {value or '—'}")
        elif isinstance(value, dict):
            diffs.append(
                f"{key}: {previous.get('risk') if isinstance(previous, dict) else '—'}"
                f" → {value.get('risk') or '—'}（{value.get('summary') or ''}）"
            )
    return "；".join(diffs) if diffs else None


async def list_items(
    session: AsyncSession,
    user: CurrentUser,
) -> list[IntelWatchlist]:
    rows = list(
        (
            await session.execute(
                select(IntelWatchlist)
                .where(IntelWatchlist.tenant_id == user.tenant_id)
                .order_by(IntelWatchlist.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return rows


async def get_item(
    session: AsyncSession,
    user: CurrentUser,
    item_id: UUID,
) -> IntelWatchlist:
    item = (
        await session.execute(
            select(IntelWatchlist).where(
                IntelWatchlist.tenant_id == user.tenant_id,
                IntelWatchlist.id == item_id,
            )
        )
    ).scalar_one_or_none()
    if item is None:
        raise NotFoundError()
    return item


async def create_item(
    session: AsyncSession,
    user: CurrentUser,
    *,
    ent_name: str,
    label: str | None,
    sections: str | None,
) -> IntelWatchlist:
    settings = get_settings()
    count = int(
        (
            await session.execute(
                select(func.count())
                .select_from(IntelWatchlist)
                .where(
                    IntelWatchlist.tenant_id == user.tenant_id,
                    IntelWatchlist.active.is_(True),
                )
            )
        ).scalar_one()
    )
    if settings.INTEL_WATCHLIST_MAX_ITEMS and count >= settings.INTEL_WATCHLIST_MAX_ITEMS:
        raise ValidationAppError(
            f"监控对象上限 {settings.INTEL_WATCHLIST_MAX_ITEMS} 个，请先停用旧对象"
        )
    existing = (
        await session.execute(
            select(IntelWatchlist).where(
                IntelWatchlist.tenant_id == user.tenant_id,
                IntelWatchlist.ent_name == ent_name.strip(),
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise ConflictError("该主体已在监控列表中")
    item = IntelWatchlist(
        tenant_id=user.tenant_id,
        ent_name=ent_name.strip(),
        label=label,
        sections=(sections or DEFAULT_WATCH_SECTIONS).strip(),
        created_by=user.id,
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return item


async def update_item(
    session: AsyncSession,
    user: CurrentUser,
    item_id: UUID,
    *,
    label: str | None,
    sections: str | None,
    active: bool | None,
) -> IntelWatchlist:
    item = await get_item(session, user, item_id)
    if label is not None:
        item.label = label
    if sections is not None:
        item.sections = sections.strip() or DEFAULT_WATCH_SECTIONS
    if active is not None:
        item.active = active
    await session.commit()
    await session.refresh(item)
    return item


async def delete_item(
    session: AsyncSession,
    user: CurrentUser,
    item_id: UUID,
) -> None:
    item = await get_item(session, user, item_id)
    await session.delete(item)
    await session.commit()


async def run_item(
    session: AsyncSession,
    item: IntelWatchlist,
    adapter: EnterpriseIntelAdapter,
) -> tuple[IntelWatchlist, DataResult[dict[str, Any]]]:
    units = adapter.estimate_units(TOOL, sections=item.sections)
    reservation = await reserve_paid_call(item.tenant_id, amount=units)
    try:
        result = await adapter.due_diligence(
            item.ent_name,
            sections=item.sections,
            include_judicial=False,
            digest_only=True,
        )
    except Exception:
        await release_paid_call(reservation)
        raise
    if not result.is_success or not result.charged:
        await release_paid_call(reservation)
    else:
        actual = min(result.billing_units or units, units)
        if actual < reservation.amount:
            await release_paid_call(
                QuotaReservation(
                    tenant_id=reservation.tenant_id,
                    periods=reservation.periods,
                    amount=reservation.amount - actual,
                )
            )
    await record_gateway_call(
        session,
        tenant_id=item.tenant_id,
        pcode=f"mcp:{TOOL}",
        operation="intel_watchlist.check",
        status=result.status.value,
        order_no=result.order_no,
        charged=result.charged,
        rows=result.api_calls or units,
        query_hash_value=query_hash({"ent_name": item.ent_name, "sections": item.sections}),
        resource_type="intel_watchlist",
        resource_id=str(item.id),
        created_by=item.created_by,
    )
    now = datetime.now(UTC)
    item.last_checked_at = now
    item.last_status = result.status.value
    if result.is_success and isinstance(result.data, dict):
        digest = _digest(result.data)
        changes = _changes(item.last_digest, digest)
        item.last_digest = digest
        item.last_error = None
        await session.commit()
        if changes and item.created_by is not None:
            await NotificationService(session).notify(
                tenant_id=item.tenant_id,
                user_id=item.created_by,
                notification_type="intel_watchlist",
                title=f"监控主体风险变化：{item.label or item.ent_name}",
                body=changes,
                resource_type="intel_watchlist",
                resource_id=str(item.id),
            )
    else:
        item.last_error = result.error.message if result.error else "查询失败"
        await session.commit()
    await session.refresh(item)
    return item, result


async def run_due_watchlist() -> int:
    settings = get_settings()
    if not settings.INTEL_WATCHLIST_ENABLED:
        return 0
    adapter = None
    from app.modules.gateway.deps import get_enterprise_intel_adapter

    adapter = get_enterprise_intel_adapter()
    executed = 0
    async with SessionLocal() as session:
        items = list(
            (
                await session.execute(
                    select(IntelWatchlist)
                    .where(IntelWatchlist.active.is_(True))
                    .order_by(IntelWatchlist.last_checked_at.asc().nullsfirst())
                    .limit(settings.INTEL_WATCHLIST_BATCH_SIZE)
                )
            )
            .scalars()
            .all()
        )
        for item in items:
            try:
                await run_item(session, item, adapter)
                executed += 1
            except Exception:
                logger.exception("intel watchlist check failed item=%s", item.id)
                item.last_status = "failed"
                item.last_error = "check failed"
                item.last_checked_at = datetime.now(UTC)
                await session.commit()
    return executed
