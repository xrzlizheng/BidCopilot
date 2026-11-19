import pytest

from app.config import get_settings
from app.core.errors import QuotaExceededError
from app.modules.costs.quota import release_paid_call, reserve_paid_call
from tests.support import cleanup_tenants, seed_tenant_user


@pytest.mark.integration
async def test_paid_call_quota_reserves_and_releases(monkeypatch) -> None:
    tenant_id, _, _ = await seed_tenant_user("quota-tenant")
    settings = get_settings()
    monkeypatch.setattr(settings, "PAID_CALL_DAILY_LIMIT", 1)
    monkeypatch.setattr(settings, "PAID_CALL_MONTHLY_LIMIT", 0)
    try:
        reservation = await reserve_paid_call(tenant_id)
        assert reservation.periods
        with pytest.raises(QuotaExceededError):
            await reserve_paid_call(tenant_id)

        await release_paid_call(reservation)
        second = await reserve_paid_call(tenant_id)
        await release_paid_call(second)
    finally:
        await cleanup_tenants([tenant_id])


@pytest.mark.integration
async def test_paid_call_quota_rolls_back_both_periods(monkeypatch) -> None:
    tenant_id, _, _ = await seed_tenant_user("quota-both")
    settings = get_settings()
    monkeypatch.setattr(settings, "PAID_CALL_DAILY_LIMIT", 1)
    monkeypatch.setattr(settings, "PAID_CALL_MONTHLY_LIMIT", 1)
    try:
        reservation = await reserve_paid_call(tenant_id)
        with pytest.raises(QuotaExceededError):
            await reserve_paid_call(tenant_id)
        await release_paid_call(reservation)
        await reserve_paid_call(tenant_id)
    finally:
        await cleanup_tenants([tenant_id])
