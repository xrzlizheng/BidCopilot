import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import UUID

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert

from app.config import get_settings
from app.core.errors import QuotaExceededError
from app.db.session import SessionLocal
from app.modules.costs.models import PaidCallQuota

logger = logging.getLogger("bidcopilot.costs.quota")


@dataclass(frozen=True)
class QuotaReservation:
    tenant_id: UUID
    periods: tuple[tuple[str, date, int], ...]
    amount: int = 1


def _periods() -> list[tuple[str, date, int]]:
    settings = get_settings()
    today = datetime.now(UTC).date()
    periods: list[tuple[str, date, int]] = []
    if settings.PAID_CALL_DAILY_LIMIT > 0:
        periods.append(("day", today, settings.PAID_CALL_DAILY_LIMIT))
    if settings.PAID_CALL_MONTHLY_LIMIT > 0:
        periods.append(("month", today.replace(day=1), settings.PAID_CALL_MONTHLY_LIMIT))
    return periods


async def reserve_paid_call(tenant_id: UUID, *, amount: int = 1) -> QuotaReservation:
    periods = _periods()
    if not periods:
        return QuotaReservation(tenant_id=tenant_id, periods=(), amount=amount)
    async with SessionLocal() as session:
        for period, period_start, limit in periods:
            statement = (
                insert(PaidCallQuota)
                .values(
                    tenant_id=tenant_id,
                    period=period,
                    period_start=period_start,
                    used=amount,
                )
                .on_conflict_do_update(
                    index_elements=["tenant_id", "period", "period_start"],
                    set_={"used": PaidCallQuota.used + amount},
                    where=PaidCallQuota.used + amount <= limit,
                )
                .returning(PaidCallQuota.used)
            )
            row = (await session.execute(statement)).first()
            if row is None:
                await session.rollback()
                label = "日" if period == "day" else "月"
                raise QuotaExceededError(
                    f"{label}度付费调用配额已用尽（上限 {limit} 次）"
                )
        await session.commit()
    return QuotaReservation(tenant_id=tenant_id, periods=tuple(periods), amount=amount)


async def release_paid_call(reservation: QuotaReservation) -> None:
    if not reservation.periods:
        return
    try:
        async with SessionLocal() as session:
            for period, period_start, _ in reservation.periods:
                await session.execute(
                    update(PaidCallQuota)
                    .where(
                        PaidCallQuota.tenant_id == reservation.tenant_id,
                        PaidCallQuota.period == period,
                        PaidCallQuota.period_start == period_start,
                        PaidCallQuota.used >= reservation.amount,
                    )
                    .values(used=PaidCallQuota.used - reservation.amount)
                )
            await session.commit()
    except Exception:
        logger.warning("failed to release paid-call quota", exc_info=True)
