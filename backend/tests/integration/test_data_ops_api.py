from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.core.security import hash_password
from app.db.session import SessionLocal
from app.main import app
from app.modules.audit.models import AuditEvent
from app.modules.gateway.contract import DataResult, SourceStatus
from app.modules.gateway.deps import get_keeprisk_adapter
from app.modules.gateway.normalize import NormalizedOpportunity
from app.modules.identity.models import Role, Tenant, User, UserRole
from app.modules.identity.roles import SYSTEM_ROLES
from app.modules.opportunities.models import SourceSyncRun


def _item() -> NormalizedOpportunity:
    fetched_at = datetime(2026, 10, 21, tzinfo=UTC)
    return NormalizedOpportunity(
        source="keeprisk:E_007_1271",
        source_id="cost-notice-1",
        dedup_key="cost-notice-1",
        title="数据运营测试项目",
        project_code="COST-001",
        project_name="数据运营测试项目",
        buyer="某采购人",
        agency=None,
        winner=None,
        region="辽宁省",
        industry=["信息通信"],
        budget_amount=Decimal("1000000"),
        win_bid_amount=None,
        publish_at=fetched_at,
        info_type="tender",
        source_url="https://example.com/cost-notice-1",
        detail_status="list_only",
        confidence="high",
        content_html=None,
        content_text=None,
        content_fetched_at=None,
        fetched_at=fetched_at,
    )


class FakeAdapter:
    async def fetch_page(self, query, *, page_no: int = 1) -> DataResult:
        return DataResult(
            status=SourceStatus.OK,
            data=[_item()],
            source="keeprisk:E_007_1271",
            fetched_at=datetime.now(UTC),
            raw_ref="test://list",
            order_no="order-list-1",
            charged=True,
        )

    async def fetch_detail(self, ids: list[str]) -> DataResult:
        return DataResult(
            status=SourceStatus.OK,
            data=[_item()],
            source="keeprisk:E_007_1271",
            fetched_at=datetime.now(UTC),
            raw_ref="test://detail",
            order_no="order-detail-1",
            charged=True,
        )


async def _seed_tenant(label: str) -> tuple[UUID, str]:
    async with SessionLocal() as session:
        roles: dict[str, Role] = {}
        for code, spec in SYSTEM_ROLES.items():
            role = (
                await session.execute(
                    select(Role).where(Role.tenant_id.is_(None), Role.code == code)
                )
            ).scalar_one_or_none()
            if role is None:
                role = Role(
                    tenant_id=None,
                    code=code,
                    name=spec["name"],
                    permissions=list(spec["permissions"]),
                    is_system=True,
                )
                session.add(role)
                await session.flush()
            else:
                role.name = spec["name"]
                role.permissions = list(spec["permissions"])
            roles[code] = role
        slug = f"{label}-{uuid4().hex[:10]}"
        tenant = Tenant(name=label, slug=slug, kind="customer")
        session.add(tenant)
        await session.flush()
        email = f"{slug}@example.com"
        user = User(
            tenant_id=tenant.id,
            email=email,
            password_hash=hash_password("Password123!"),
            display_name=label,
        )
        session.add(user)
        await session.flush()
        session.add(
            UserRole(tenant_id=tenant.id, user_id=user.id, role_id=roles["tenant_admin"].id, scope_type="tenant", scope_id=tenant.id)
        )
        await session.commit()
        return tenant.id, email


async def _cleanup(tenant_ids: list[UUID]) -> None:
    async with SessionLocal() as session:
        await session.execute(delete(AuditEvent).where(AuditEvent.tenant_id.in_(tenant_ids)))
        await session.execute(delete(Tenant).where(Tenant.id.in_(tenant_ids)))
        await session.commit()


@pytest.mark.integration
async def test_data_ops_cost_and_health_aggregation() -> None:
    tenant_id, email = await _seed_tenant("data-ops-a")
    other_tenant_id, other_email = await _seed_tenant("data-ops-b")
    app.dependency_overrides[get_keeprisk_adapter] = lambda: FakeAdapter()
    try:
        async with SessionLocal() as session:
            session.add(
                SourceSyncRun(
                    tenant_id=tenant_id,
                    source="keeprisk:E_007_1271",
                    pcode="E_007_1271",
                    query_json={},
                    status="failed",
                    error_code="transport_error",
                    error_message="timeout",
                )
            )
            await session.commit()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            login = await client.post(
                "/api/v1/auth/login", json={"email": email, "password": "Password123!"}
            )
            assert login.status_code == 200, login.text
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

            synced = await client.post("/api/v1/opportunities/sync", json={}, headers=headers)
            assert synced.status_code == 200, synced.text
            listing = await client.get("/api/v1/opportunities", headers=headers)
            opportunity_id = listing.json()["items"][0]["id"]
            detail = await client.get(f"/api/v1/opportunities/{opportunity_id}", headers=headers)
            assert detail.status_code == 200, detail.text

            ops = await client.get("/api/v1/analytics/data-ops?days=30", headers=headers)
            assert ops.status_code == 200, ops.text
            body = ops.json()
            assert body["costs"]["total_calls"] == 2
            assert body["costs"]["charged_calls"] == 2
            assert body["costs"]["failed_calls"] == 0
            assert body["costs"]["rows"] == 2
            assert body["costs"]["by_pcode"] == [
                {
                    "pcode": "E_007_1271",
                    "calls": 2,
                    "charged_calls": 2,
                    "failed_calls": 0,
                    "rows": 2,
                }
            ]
            orders = {row["order_no"] for row in body["costs"]["recent"]}
            assert orders == {"order-list-1", "order-detail-1"}
            assert body["sources"][0]["runs"] == 2
            assert body["sources"][0]["succeeded"] == 1
            assert body["sources"][0]["failed"] == 1
            assert body["sources"][0]["success_rate"] == 50.0
            assert body["sources"][0]["last_failed_error"] == "timeout"

            other_login = await client.post(
                "/api/v1/auth/login",
                json={"email": other_email, "password": "Password123!"},
            )
            other_headers = {"Authorization": f"Bearer {other_login.json()['access_token']}"}
            other_ops = await client.get("/api/v1/analytics/data-ops", headers=other_headers)
            assert other_ops.status_code == 200, other_ops.text
            assert other_ops.json()["costs"]["total_calls"] == 0
            assert other_ops.json()["sources"] == []
    finally:
        app.dependency_overrides.pop(get_keeprisk_adapter, None)
        await _cleanup([tenant_id, other_tenant_id])


@pytest.mark.integration
async def test_cost_ledger_order_no_is_idempotent() -> None:
    from app.modules.costs.models import CostLedger
    from app.modules.costs.service import record_gateway_call
    from tests.support import cleanup_tenants, seed_tenant_user

    tenant_id, _, _ = await seed_tenant_user("cost-dedupe")
    try:
        async with SessionLocal() as session:
            first = await record_gateway_call(
                session,
                tenant_id=tenant_id,
                pcode="E_007_1271",
                operation="opportunity.list",
                status="ok",
                order_no="dup-order-1",
                charged=True,
                rows=1,
            )
            second = await record_gateway_call(
                session,
                tenant_id=tenant_id,
                pcode="E_007_1271",
                operation="opportunity.list",
                status="ok",
                order_no="dup-order-1",
                charged=True,
                rows=1,
            )
            assert second.id == first.id
            rows = list(
                (
                    await session.execute(
                        select(CostLedger).where(
                            CostLedger.tenant_id == tenant_id,
                            CostLedger.order_no == "dup-order-1",
                        )
                    )
                ).scalars()
            )
            assert len(rows) == 1
    finally:
        await cleanup_tenants([tenant_id])
