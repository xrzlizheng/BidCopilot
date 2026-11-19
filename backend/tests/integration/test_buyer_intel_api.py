from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db.session import SessionLocal
from app.main import app
from app.modules.assets.models import Company
from app.modules.audit.models import AuditEvent
from app.modules.costs.models import CostLedger
from app.modules.gateway.contract import DataError, DataResult, SourceStatus
from app.modules.gateway.deps import get_company_intel_adapter
from app.modules.gateway.normalize import NormalizedOpportunity
from app.modules.opportunities.models import Opportunity
from tests.support import cleanup_tenants, login, seed_tenant_user

BUYER = "某市大数据局"
INCUMBENT = "既有供应商甲"


def _award(
    winner: str, *, days_ago: int, amount: str | None, title: str
) -> NormalizedOpportunity:
    now = datetime.now(UTC)
    return NormalizedOpportunity(
        source="keeprisk:E_007_1271",
        source_id=title,
        dedup_key=f"intel-{title}",
        title=title,
        project_code=None,
        project_name=None,
        buyer=BUYER,
        agency=None,
        winner=winner,
        region="广东",
        industry=["政府采购"],
        budget_amount=None,
        win_bid_amount=Decimal(amount) if amount else None,
        publish_at=now - timedelta(days=days_ago),
        info_type="award",
        source_url="https://example.com/notice",
        detail_status="list_only",
        confidence="high",
        content_html=None,
        content_text=None,
        content_fetched_at=None,
        fetched_at=now,
    )


class FakeIntelAdapter:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[str, str]] = []
        self.info_types_seen: list[list[str] | None] = []

    async def fetch_bids(
        self,
        company_name: str,
        *,
        role: str,
        page_size: int = 50,
        info_types: list[str] | None = None,
    ) -> DataResult[list[NormalizedOpportunity]]:
        self.calls.append((company_name, role))
        self.info_types_seen.append(info_types)
        now = datetime.now(UTC)
        if self.fail:
            return DataResult(
                status=SourceStatus.UNAUTHORIZED,
                data=None,
                source="keeprisk:E_007_1271",
                fetched_at=now,
                error=DataError(code="103", message="app code rejected"),
            )
        rows = [
            _award(INCUMBENT, days_ago=30, amount="1200000", title="运维一期中标"),
            _award(INCUMBENT, days_ago=180, amount="900000", title="运维二期中标"),
            _award(INCUMBENT, days_ago=420, amount="700000", title="运维三期中标"),
            _award("新面孔乙", days_ago=10, amount=None, title="数据中台中标"),
            _award("新面孔乙", days_ago=400, amount=None, title="数据中台二期中标"),
            NormalizedOpportunity(
                source="keeprisk:E_007_1271",
                source_id="招标一条",
                dedup_key="intel-tender",
                title="招标一条",
                project_code=None,
                project_name=None,
                buyer=BUYER,
                agency=None,
                winner=None,
                region="广东",
                industry=["政府采购"],
                budget_amount=Decimal("800000"),
                win_bid_amount=None,
                publish_at=now - timedelta(days=5),
                info_type="tender",
                source_url=None,
                detail_status="list_only",
                confidence="high",
                content_html=None,
                content_text=None,
                content_fetched_at=None,
                fetched_at=now,
            ),
        ]
        return DataResult(
            status=SourceStatus.OK,
            data=rows,
            source="keeprisk:E_007_1271",
            fetched_at=now,
            raw_ref="test://buyer-intel",
            order_no="test-order-intel",
            charged=True,
        )

    async def fetch_profile(self, company_name: str) -> DataResult:
        raise AssertionError("buyer intel must not call profile")


async def _seed_opportunities(tenant_id: UUID) -> tuple[UUID, UUID]:
    """返回 (有采购人的机会 id, 无采购人的机会 id)。"""
    now = datetime.now(UTC)
    with_buyer = Opportunity(
        tenant_id=tenant_id,
        source="keeprisk:E_007_1271",
        dedup_key="intel-target",
        title="某市政务云运维项目",
        buyer=BUYER,
        industry=["政府采购"],
        info_type="tender",
        detail_status="list_only",
        confidence="high",
        fetched_at=now,
    )
    without_buyer = Opportunity(
        tenant_id=tenant_id,
        source="keeprisk:E_007_1271",
        dedup_key="intel-no-buyer",
        title="采购人未披露项目",
        buyer=None,
        industry=["政府采购"],
        info_type="tender",
        detail_status="list_only",
        confidence="high",
        fetched_at=now,
    )
    tracked = Company(tenant_id=tenant_id, name=INCUMBENT)
    async with SessionLocal() as session:
        session.add_all([with_buyer, without_buyer, tracked])
        await session.commit()
        return with_buyer.id, without_buyer.id


@pytest.mark.integration
async def test_buyer_intel_sync_aggregates_incumbent_suppliers() -> None:
    tenant_id, _, email = await seed_tenant_user("intel-owner")
    _, _, engineer_email = await seed_tenant_user("intel-engineer", "bid_engineer", tenant_id)
    other_tenant_id, _, other_email = await seed_tenant_user("intel-outsider")
    opportunity_id, no_buyer_id = await _seed_opportunities(tenant_id)
    adapter = FakeIntelAdapter()
    app.dependency_overrides[get_company_intel_adapter] = lambda: adapter
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = await login(client, email)

            missing = await client.get(
                f"/api/v1/opportunities/{opportunity_id}/buyer-intel", headers=headers
            )
            assert missing.status_code == 404

            synced = await client.post(
                f"/api/v1/opportunities/{opportunity_id}/buyer-intel/sync", headers=headers
            )
            assert synced.status_code == 200, synced.text
            body = synced.json()
            assert adapter.calls == [(BUYER, "tenderer")]
            assert adapter.info_types_seen == [["3"]]
            assert body["status"] == "ok"
            assert body["rows_fetched"] == 6
            assert body["recent_wins_12m"] == 3
            assert [row["winner"] for row in body["top_winners"]] == [INCUMBENT, "新面孔乙"]
            assert body["top_winners"][0]["wins"] == 3
            assert body["top_winners"][0]["recent_wins_12m"] == 2
            assert body["top_winners"][0]["tracked"] is True
            assert body["top_winners"][0]["total_amount"] == "2800000.00"
            assert body["top_winners"][1]["wins"] == 2
            assert body["top_winners"][1]["recent_wins_12m"] == 1
            assert body["top_winners"][1]["tracked"] is False
            assert body["top_winners"][1]["total_amount"] is None
            # 两次中标里只有 1 次在近 12 个月，不能算既有供应商。
            assert [row["winner"] for row in body["existing_suppliers"]] == [INCUMBENT]
            assert len(body["winner_rows"]) == 5
            assert body["charged"] is True
            assert body["gateway_order_no"] == "test-order-intel"

            cached = await client.get(
                f"/api/v1/opportunities/{opportunity_id}/buyer-intel", headers=headers
            )
            assert cached.status_code == 200
            assert cached.json()["id"] == body["id"]

            no_buyer = await client.post(
                f"/api/v1/opportunities/{no_buyer_id}/buyer-intel/sync", headers=headers
            )
            assert no_buyer.status_code == 422, no_buyer.text

            engineer = await login(client, engineer_email)
            engineer_read = await client.get(
                f"/api/v1/opportunities/{opportunity_id}/buyer-intel", headers=engineer
            )
            assert engineer_read.status_code == 200
            engineer_sync = await client.post(
                f"/api/v1/opportunities/{opportunity_id}/buyer-intel/sync", headers=engineer
            )
            assert engineer_sync.status_code == 403, engineer_sync.text

            outsider = await login(client, other_email)
            cross_tenant = await client.get(
                f"/api/v1/opportunities/{opportunity_id}/buyer-intel", headers=outsider
            )
            assert cross_tenant.status_code == 404

        async with SessionLocal() as session:
            ledger = (
                await session.execute(
                    select(CostLedger).where(
                        CostLedger.tenant_id == tenant_id,
                        CostLedger.operation == "opportunity.buyer_intel",
                    )
                )
            ).scalars().all()
            assert [row.status for row in ledger] == ["ok"]
            assert ledger[0].charged is True
            audit = (
                await session.execute(
                    select(AuditEvent).where(
                        AuditEvent.tenant_id == tenant_id,
                        AuditEvent.action == "opportunity.buyer_intel_sync",
                    )
                )
            ).scalars().all()
            assert len(audit) == 1
            assert audit[0].data["existing_suppliers"] == 1
    finally:
        app.dependency_overrides.clear()
        async with SessionLocal() as session:
            await session.execute(
                Opportunity.__table__.delete().where(Opportunity.tenant_id == tenant_id)
            )
            await session.execute(
                Company.__table__.delete().where(Company.tenant_id == tenant_id)
            )
            await session.commit()
        await cleanup_tenants([tenant_id, other_tenant_id])


@pytest.mark.integration
async def test_buyer_intel_first_failure_has_empty_aggregation() -> None:
    tenant_id, _, email = await seed_tenant_user("intel-first-fail")
    opportunity_id, _ = await _seed_opportunities(tenant_id)
    app.dependency_overrides[get_company_intel_adapter] = lambda: FakeIntelAdapter(fail=True)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = await login(client, email)
            failed = await client.post(
                f"/api/v1/opportunities/{opportunity_id}/buyer-intel/sync", headers=headers
            )
            assert failed.status_code == 200, failed.text
            body = failed.json()
            assert body["status"] == "unauthorized"
            assert body["error_code"] == "103"
            assert body["top_winners"] == []
            assert body["existing_suppliers"] == []
            assert body["synced_at"] is None
            assert body["charged"] is False
    finally:
        app.dependency_overrides.clear()
        async with SessionLocal() as session:
            await session.execute(
                Opportunity.__table__.delete().where(Opportunity.tenant_id == tenant_id)
            )
            await session.execute(Company.__table__.delete().where(Company.tenant_id == tenant_id))
            await session.commit()
        await cleanup_tenants([tenant_id])


@pytest.mark.integration
async def test_buyer_intel_failure_keeps_upstream_status_and_last_good() -> None:
    tenant_id, _, email = await seed_tenant_user("intel-fail")
    opportunity_id, _ = await _seed_opportunities(tenant_id)
    adapter = FakeIntelAdapter()
    app.dependency_overrides[get_company_intel_adapter] = lambda: adapter
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = await login(client, email)
            good = await client.post(
                f"/api/v1/opportunities/{opportunity_id}/buyer-intel/sync", headers=headers
            )
            assert good.json()["status"] == "ok"

            adapter.fail = True
            failed = await client.post(
                f"/api/v1/opportunities/{opportunity_id}/buyer-intel/sync", headers=headers
            )
            assert failed.status_code == 200, failed.text
            body = failed.json()
            assert body["status"] == "unauthorized"
            assert body["error_code"] == "103"
            assert body["error_message"] == "app code rejected"
            # 溯源仍属于上次成功调用，失败只改状态与错误，不能配错 provenance。
            assert body["charged"] is True
            assert body["gateway_order_no"] == "test-order-intel"
            assert body["synced_at"] == good.json()["synced_at"]
            assert body["rows_fetched"] == 6
            # 一次瞬时失败不能清空上次成功聚合。
            assert body["top_winners"], body
            assert [row["winner"] for row in body["existing_suppliers"]] == [INCUMBENT]

            listing = await client.get("/api/v1/notifications", headers=headers)
            assert listing.json()["items"] == []
    finally:
        app.dependency_overrides.clear()
        async with SessionLocal() as session:
            await session.execute(
                Opportunity.__table__.delete().where(Opportunity.tenant_id == tenant_id)
            )
            await session.execute(Company.__table__.delete().where(Company.tenant_id == tenant_id))
            await session.commit()
        await cleanup_tenants([tenant_id])


@pytest.mark.integration
async def test_buyer_intel_sync_survives_bookkeeping_failure(monkeypatch) -> None:
    """付费调用完成后的记账失败不得把同步请求变成 500——用户重试会二次计费。"""
    tenant_id, _, email = await seed_tenant_user("intel-bookkeeping")
    opportunity_id, _ = await _seed_opportunities(tenant_id)
    app.dependency_overrides[get_company_intel_adapter] = lambda: FakeIntelAdapter()

    async def _explode(*args: object, **kwargs: object) -> None:
        raise RuntimeError("cost ledger unavailable")

    monkeypatch.setattr("app.modules.opportunities.router.record_gateway_call", _explode)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = await login(client, email)
            synced = await client.post(
                f"/api/v1/opportunities/{opportunity_id}/buyer-intel/sync", headers=headers
            )
            assert synced.status_code == 200, synced.text
            assert synced.json()["status"] == "ok"

            cached = await client.get(
                f"/api/v1/opportunities/{opportunity_id}/buyer-intel", headers=headers
            )
            assert cached.status_code == 200
            assert cached.json()["top_winners"]

        async with SessionLocal() as session:
            ledger = (
                await session.execute(
                    select(CostLedger).where(
                        CostLedger.tenant_id == tenant_id,
                        CostLedger.operation == "opportunity.buyer_intel",
                    )
                )
            ).scalars().all()
            assert ledger == []
            audit = (
                await session.execute(
                    select(AuditEvent).where(
                        AuditEvent.tenant_id == tenant_id,
                        AuditEvent.action == "opportunity.buyer_intel_sync",
                    )
                )
            ).scalars().all()
            assert audit == []
    finally:
        app.dependency_overrides.clear()
        async with SessionLocal() as session:
            await session.execute(
                Opportunity.__table__.delete().where(Opportunity.tenant_id == tenant_id)
            )
            await session.execute(Company.__table__.delete().where(Company.tenant_id == tenant_id))
            await session.commit()
        await cleanup_tenants([tenant_id])


@pytest.mark.integration
async def test_buyer_intel_sync_conflicts_when_locked() -> None:
    """同一采购人情报的并发查询必须被 409 挡下，锁释放后可成功。"""
    from app.db.locks import advisory_lock

    tenant_id, _, email = await seed_tenant_user("intel-lock")
    opportunity_id, _ = await _seed_opportunities(tenant_id)
    adapter = FakeIntelAdapter()
    app.dependency_overrides[get_company_intel_adapter] = lambda: adapter
    lock_key = f"opportunity-buyer-intel:{tenant_id}:{opportunity_id}"
    try:
        async with advisory_lock(lock_key) as acquired:
            assert acquired is True
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                headers = await login(client, email)
                conflict = await client.post(
                    f"/api/v1/opportunities/{opportunity_id}/buyer-intel/sync",
                    headers=headers,
                )
                assert conflict.status_code == 409, conflict.text
                assert "正在同步" in conflict.text

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = await login(client, email)
            ok = await client.post(
                f"/api/v1/opportunities/{opportunity_id}/buyer-intel/sync", headers=headers
            )
            assert ok.status_code == 200, ok.text
            assert ok.json()["status"] == "ok"
        assert len(adapter.calls) == 1
    finally:
        app.dependency_overrides.clear()
        async with SessionLocal() as session:
            await session.execute(
                Opportunity.__table__.delete().where(Opportunity.tenant_id == tenant_id)
            )
            await session.execute(Company.__table__.delete().where(Company.tenant_id == tenant_id))
            await session.commit()
        await cleanup_tenants([tenant_id])
