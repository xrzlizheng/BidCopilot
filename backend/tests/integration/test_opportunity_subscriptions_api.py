import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db.session import SessionLocal
from app.main import app
from app.modules.audit.models import AuditEvent
from app.modules.costs.models import CostLedger
from app.modules.gateway.contract import DataError, DataResult, SourceStatus
from app.modules.gateway.deps import get_keeprisk_adapter
from app.modules.gateway.normalize import normalize_page
from app.modules.notifications.models import Notification
from app.modules.opportunities.models import Opportunity, OpportunitySubscription, SourceSyncRun
from tests.support import cleanup_tenants, login, seed_tenant_user

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "keeprisk"


class WarningAdapter:
    """返回一条缺 uid 的公告，用于验证 missing_uid 告警进入同步运行记录。"""

    def __init__(self) -> None:
        self.list_calls = 0

    async def fetch_page(self, query, *, page_no: int = 1) -> DataResult:
        self.list_calls += 1
        payload = [
            {
                "title": "无 uid 的测试公告",
                "publishDate": "2026-10-16 00:00:00",
                "infoType": "1",
                "projectNo": "P-WARN",
            }
        ]
        items, warnings = normalize_page(payload, fetched_at=datetime.now(UTC))
        return DataResult(
            status=SourceStatus.OK,
            data=items,
            source="keeprisk:E_007_1271",
            fetched_at=datetime.now(UTC),
            raw_ref="test://warn",
            order_no="test-order-warn",
            charged=True,
            warnings=warnings,
        )

    async def fetch_detail(self, ids: list[str]) -> DataResult:
        raise AssertionError("warning sync must not fetch detail")


class SubscriptionAdapter:
    """列表夹具适配器；fail=True 时原样返回上游不可用状态。"""

    def __init__(self, *, fail: bool = False) -> None:
        self.payload = json.loads((FIXTURES / "e_007_1271_list.json").read_text(encoding="utf-8"))
        self.fail = fail
        self.list_calls = 0

    async def fetch_page(self, query, *, page_no: int = 1) -> DataResult:
        self.list_calls += 1
        if self.fail:
            return DataResult(
                status=SourceStatus.UNAVAILABLE,
                data=None,
                source="keeprisk:E_007_1271",
                fetched_at=datetime.now(UTC),
                error=DataError(code="107", message="upstream unavailable"),
            )
        items, warnings = normalize_page(self.payload["data"], fetched_at=datetime.now(UTC))
        return DataResult(
            status=SourceStatus.OK,
            data=items,
            source="keeprisk:E_007_1271",
            fetched_at=datetime.now(UTC),
            raw_ref="test://subscription-list",
            order_no=f"test-order-subscription-{self.list_calls}",
            charged=True,
            warnings=warnings,
        )

    async def fetch_detail(self, ids: list[str]) -> DataResult:
        raise AssertionError("subscription sync must not fetch detail")


@pytest.mark.integration
async def test_subscription_crud_validation_and_isolation() -> None:
    tenant_id, _, email = await seed_tenant_user("sub-owner")
    _, _, engineer_email = await seed_tenant_user("sub-engineer", "bid_engineer", tenant_id)
    _, _, manager_email = await seed_tenant_user("sub-manager", "bid_manager", tenant_id)
    other_tenant_id, _, other_email = await seed_tenant_user("sub-outsider")
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = await login(client, email)
            created = await client.post(
                "/api/v1/opportunity-subscriptions",
                json={"name": "系统集成监控", "interval_minutes": 60, "query": {"keywords": ["系统集成"]}},
                headers=headers,
            )
            assert created.status_code == 200, created.text
            subscription_id = created.json()["id"]
            assert created.json()["enabled"] is True
            assert created.json()["interval_minutes"] == 60

            duplicate = await client.post(
                "/api/v1/opportunity-subscriptions",
                json={"name": "系统集成监控"},
                headers=headers,
            )
            assert duplicate.status_code == 409, duplicate.text

            manager_headers = await login(client, manager_email)
            foreign_patch = await client.patch(
                f"/api/v1/opportunity-subscriptions/{subscription_id}",
                json={"enabled": False},
                headers=manager_headers,
            )
            assert foreign_patch.status_code == 404, foreign_patch.text
            foreign_delete = await client.delete(
                f"/api/v1/opportunity-subscriptions/{subscription_id}",
                headers=manager_headers,
            )
            assert foreign_delete.status_code == 404, foreign_delete.text
            still_there = await client.get(
                "/api/v1/opportunity-subscriptions", headers=headers
            )
            assert any(
                item["id"] == subscription_id for item in still_there.json()["items"]
            )

            too_fast = await client.post(
                "/api/v1/opportunity-subscriptions",
                json={"name": "过频", "interval_minutes": 5},
                headers=headers,
            )
            assert too_fast.status_code == 422, too_fast.text
            assert "30" in too_fast.text

            for index in range(9):
                extra = await client.post(
                    "/api/v1/opportunity-subscriptions",
                    json={"name": f"订阅 {index}"},
                    headers=headers,
                )
                assert extra.status_code == 200, extra.text
            over_limit = await client.post(
                "/api/v1/opportunity-subscriptions",
                json={"name": "越界订阅"},
                headers=headers,
            )
            assert over_limit.status_code == 409, over_limit.text

            engineer_headers = await login(client, engineer_email)
            engineer_list = await client.get(
                "/api/v1/opportunity-subscriptions", headers=engineer_headers
            )
            assert engineer_list.status_code == 200
            assert engineer_list.json()["total"] == 0
            engineer_write = await client.post(
                "/api/v1/opportunity-subscriptions",
                json={"name": "工程师订阅"},
                headers=engineer_headers,
            )
            assert engineer_write.status_code == 403, engineer_write.text

            outsider = await login(client, other_email)
            cross_tenant = await client.patch(
                f"/api/v1/opportunity-subscriptions/{subscription_id}",
                json={"enabled": False},
                headers=outsider,
            )
            assert cross_tenant.status_code == 404

            patched = await client.patch(
                f"/api/v1/opportunity-subscriptions/{subscription_id}",
                json={"enabled": False, "interval_minutes": 120},
                headers=headers,
            )
            assert patched.status_code == 200, patched.text
            assert patched.json()["enabled"] is False
            assert patched.json()["interval_minutes"] == 120

            deleted = await client.delete(
                f"/api/v1/opportunity-subscriptions/{subscription_id}", headers=headers
            )
            assert deleted.status_code == 200
            assert deleted.json() == {"deleted": True}
            after_delete = await client.get(
                "/api/v1/opportunity-subscriptions", headers=headers
            )
            assert all(
                item["id"] != subscription_id for item in after_delete.json()["items"]
            )
    finally:
        await cleanup_tenants([tenant_id, other_tenant_id])


@pytest.mark.integration
async def test_subscription_run_notifies_new_rows_then_dedupes() -> None:
    tenant_id, _, email = await seed_tenant_user("sub-run")
    _, _, manager_email = await seed_tenant_user("sub-run-manager", "bid_manager", tenant_id)
    adapter = SubscriptionAdapter()
    app.dependency_overrides[get_keeprisk_adapter] = lambda: adapter
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = await login(client, email)
            created = await client.post(
                "/api/v1/opportunity-subscriptions",
                json={"name": "政务云", "query": {"keywords": ["政务云"]}},
                headers=headers,
            )
            subscription_id = created.json()["id"]

            first = await client.post(
                f"/api/v1/opportunity-subscriptions/{subscription_id}/run", headers=headers
            )
            assert first.status_code == 200, first.text
            assert first.json()["status"] == "succeeded"
            assert first.json()["rows_fetched"] == 3
            assert first.json()["rows_upserted"] == 3
            assert first.json()["rows_new"] == 3
            assert adapter.list_calls == 1

            unread = await client.get("/api/v1/notifications/unread-count", headers=headers)
            assert unread.status_code == 200
            assert unread.json()["unread"] == 1

            manager_headers = await login(client, manager_email)
            assert (
                await client.get("/api/v1/notifications/unread-count", headers=manager_headers)
            ).json()["unread"] == 0
            assert (
                await client.get("/api/v1/notifications", headers=manager_headers)
            ).json()["items"] == []
            listing = await client.get("/api/v1/notifications", headers=headers)
            assert listing.json()["items"][0]["type"] == "sync_new_rows"
            assert "新增 3 条商机" in listing.json()["items"][0]["title"]

            second = await client.post(
                f"/api/v1/opportunity-subscriptions/{subscription_id}/run", headers=headers
            )
            assert second.json()["rows_new"] == 0
            assert second.json()["rows_upserted"] == 3
            unread_after = await client.get("/api/v1/notifications/unread-count", headers=headers)
            assert unread_after.json()["unread"] == 1

            window_start = datetime.now(UTC) - timedelta(minutes=5)
            fresh = await client.get(
                "/api/v1/opportunities",
                params={"created_after": window_start.isoformat()},
                headers=headers,
            )
            assert fresh.status_code == 200, fresh.text
            assert fresh.json()["total"] == 3
            stale = await client.get(
                "/api/v1/opportunities",
                params={"created_after": "2020-01-01T00:00:00+00:00"},
                headers=headers,
            )
            assert stale.json()["total"] == 3
            future = await client.get(
                "/api/v1/opportunities",
                params={"created_after": "2999-01-01T00:00:00+00:00"},
                headers=headers,
            )
            assert future.json()["total"] == 0

            marked = await client.post(
                "/api/v1/notifications/read", json={"all": True}, headers=headers
            )
            assert marked.status_code == 200, marked.text
            assert marked.json()["updated"] == 1
            assert (
                await client.get("/api/v1/notifications/unread-count", headers=headers)
            ).json()["unread"] == 0
            unread_only_after = await client.get(
                "/api/v1/notifications?unread_only=true", headers=headers
            )
            assert unread_only_after.json()["items"] == []

        async with SessionLocal() as session:
            ledger_rows = (
                await session.execute(
                    select(CostLedger).where(
                        CostLedger.tenant_id == tenant_id,
                        CostLedger.operation == "opportunity.list",
                    )
                )
            ).scalars().all()
            assert len(ledger_rows) == 2
            assert all(row.charged is True for row in ledger_rows)
            audit_rows = (
                await session.execute(
                    select(AuditEvent)
                    .where(
                        AuditEvent.tenant_id == tenant_id,
                        AuditEvent.action == "opportunity.sync_scheduled",
                    )
                    .order_by(AuditEvent.seq.asc())
                )
            ).scalars().all()
            assert len(audit_rows) == 2
            assert audit_rows[0].data["rows_new"] == 3
            subscription = (
                await session.execute(
                    select(OpportunitySubscription).where(
                        OpportunitySubscription.tenant_id == tenant_id
                    )
                )
            ).scalar_one()
            assert subscription.last_run_at is not None
            assert subscription.last_sync_run_id is not None
    finally:
        app.dependency_overrides.clear()
        await cleanup_tenants([tenant_id])


@pytest.mark.integration
async def test_subscription_failure_notifies_with_upstream_status() -> None:
    tenant_id, _, email = await seed_tenant_user("sub-fail")
    adapter = SubscriptionAdapter(fail=True)
    app.dependency_overrides[get_keeprisk_adapter] = lambda: adapter
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = await login(client, email)
            created = await client.post(
                "/api/v1/opportunity-subscriptions",
                json={"name": "失败订阅"},
                headers=headers,
            )
            run = await client.post(
                f"/api/v1/opportunity-subscriptions/{created.json()['id']}/run", headers=headers
            )
            assert run.status_code == 200, run.text
            assert run.json()["status"] == "failed"
            assert run.json()["rows_fetched"] == 0

            listing = await client.get("/api/v1/notifications", headers=headers)
            items = listing.json()["items"]
            assert len(items) == 1
            assert items[0]["type"] == "sync_failed"
            assert "upstream unavailable" in items[0]["body"]

        async with SessionLocal() as session:
            notifications = (
                await session.execute(
                    select(Notification).where(Notification.tenant_id == tenant_id)
                )
            ).scalars().all()
            assert [row.type for row in notifications] == ["sync_failed"]
            ledger = (
                await session.execute(
                    select(CostLedger).where(
                        CostLedger.tenant_id == tenant_id,
                        CostLedger.operation == "opportunity.list",
                    )
                )
            ).scalars().all()
            assert [row.status for row in ledger] == ["unavailable"]
    finally:
        app.dependency_overrides.clear()
        await cleanup_tenants([tenant_id])


@pytest.mark.integration
async def test_list_order_by_budget_and_heuristic_match() -> None:
    tenant_id, _, email = await seed_tenant_user("sub-order")
    adapter = SubscriptionAdapter()
    app.dependency_overrides[get_keeprisk_adapter] = lambda: adapter
    fixture_items, _ = normalize_page(
        json.loads((FIXTURES / "e_007_1271_list.json").read_text(encoding="utf-8"))["data"],
        fetched_at=datetime.now(UTC),
    )
    anchor = fixture_items[0]
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = await login(client, email)
            await client.post(
                "/api/v1/opportunities/sync",
                json={"keywords": [], "info_types": ["1", "2", "3"]},
                headers=headers,
            )

            bad_order = await client.get(
                "/api/v1/opportunities?order_by=random", headers=headers
            )
            assert bad_order.status_code == 422, bad_order.text

            no_subscription = await client.get(
                "/api/v1/opportunities?order_by=match", headers=headers
            )
            assert no_subscription.status_code == 422, no_subscription.text
            assert "订阅" in no_subscription.text

            budget_order = await client.get(
                "/api/v1/opportunities?order_by=budget", headers=headers
            )
            assert budget_order.status_code == 200
            assert budget_order.json()["order_by"] == "budget"
            assert [item["budget_amount"] for item in budget_order.json()["items"]] == [None] * 3

            created = await client.post(
                "/api/v1/opportunity-subscriptions",
                json={
                    "name": "锚点订阅",
                    "query": {"keywords": [anchor.title[:4]], "industry_list": [anchor.industry[0]]},
                },
                headers=headers,
            )
            assert created.status_code == 200, created.text

            matched = await client.get("/api/v1/opportunities?order_by=match", headers=headers)
            assert matched.status_code == 200, matched.text
            body = matched.json()
            assert body["order_by"] == "match"
            assert body["items"][0]["id"] == str(
                await _opportunity_id_for(anchor.dedup_key)
            )
            assert body["items"][0]["match_hits"] == 2
            assert {item["match_hits"] for item in body["items"][1:]} == {0}

            disabled = await client.patch(
                f"/api/v1/opportunity-subscriptions/{created.json()['id']}",
                json={"enabled": False},
                headers=headers,
            )
            assert disabled.status_code == 200
            after_disable = await client.get(
                "/api/v1/opportunities?order_by=match", headers=headers
            )
            assert after_disable.status_code == 422, after_disable.text
    finally:
        app.dependency_overrides.clear()
        await cleanup_tenants([tenant_id])


async def _opportunity_id_for(dedup_key: str) -> UUID:
    async with SessionLocal() as session:
        return (
            await session.execute(
                select(Opportunity.id).where(Opportunity.dedup_key == dedup_key)
            )
        ).scalar_one()


@pytest.mark.integration
async def test_manual_run_conflicts_when_subscription_is_locked() -> None:
    from app.modules.opportunities.subscriptions import subscription_lock

    tenant_id, _, email = await seed_tenant_user("sub-lock")
    app.dependency_overrides[get_keeprisk_adapter] = lambda: SubscriptionAdapter()
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = await login(client, email)
            created = await client.post(
                "/api/v1/opportunity-subscriptions",
                json={"name": "并发保护"},
                headers=headers,
            )
            subscription_id = UUID(created.json()["id"])
            async with subscription_lock(subscription_id) as acquired:
                assert acquired is True
                async with subscription_lock(subscription_id) as second:
                    assert second is False
                conflict = await client.post(
                    f"/api/v1/opportunity-subscriptions/{subscription_id}/run",
                    headers=headers,
                )
                assert conflict.status_code == 409, conflict.text
                assert "正在同步" in conflict.text
    finally:
        app.dependency_overrides.clear()
        await cleanup_tenants([tenant_id])


@pytest.mark.integration
async def test_interrupted_run_is_not_charged_twice() -> None:
    tenant_id, user_id, email = await seed_tenant_user("sub-interrupted")
    adapter = SubscriptionAdapter()
    app.dependency_overrides[get_keeprisk_adapter] = lambda: adapter
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = await login(client, email)
            created = await client.post(
                "/api/v1/opportunity-subscriptions",
                json={"name": "中断恢复"},
                headers=headers,
            )
            subscription_id = created.json()["id"]
            async with SessionLocal() as session:
                unfinished = SourceSyncRun(
                    tenant_id=tenant_id,
                    source="keeprisk:E_007_1271",
                    pcode="E_007_1271",
                    query_json={},
                    status="running",
                    created_by=user_id,
                )
                session.add(unfinished)
                await session.commit()
                subscription = (
                    await session.execute(
                        select(OpportunitySubscription).where(
                            OpportunitySubscription.id == UUID(subscription_id)
                        )
                    )
                ).scalar_one()
                subscription.last_sync_run_id = unfinished.id
                await session.commit()

            response = await client.post(
                f"/api/v1/opportunity-subscriptions/{subscription_id}/run", headers=headers
            )
            assert response.status_code == 200, response.text
            assert response.json()["status"] == "failed"
            assert response.json()["error_code"] == "sync_interrupted"
            assert adapter.list_calls == 0
            notes = await client.get("/api/v1/notifications", headers=headers)
            assert [item["type"] for item in notes.json()["items"]] == ["sync_failed"]
    finally:
        app.dependency_overrides.clear()
        await cleanup_tenants([tenant_id])


@pytest.mark.integration
async def test_missing_uid_warning_surfaces_in_sync_run() -> None:
    tenant_id, _, email = await seed_tenant_user("sub-warning")
    app.dependency_overrides[get_keeprisk_adapter] = lambda: WarningAdapter()
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = await login(client, email)
            synced = await client.post(
                "/api/v1/opportunities/sync",
                json={"keywords": [], "info_types": ["1", "2", "3"]},
                headers=headers,
            )
            assert synced.status_code == 200, synced.text
            body = synced.json()
            assert body["status"] == "succeeded"
            assert body["rows_fetched"] == 1
            assert body["warnings_count"] == 1
            assert body["warnings"][0]["code"] == "missing_uid"
            assert "falls back" in body["warnings"][0]["message"]
            assert body["warnings"][0]["field"] == "uid"

            listing = await client.get("/api/v1/opportunities", headers=headers)
            items = listing.json()["items"]
            assert len(items) == 1
            assert items[0]["title"] == "无 uid 的测试公告"
            assert len(items[0]["id"]) == 36
    finally:
        app.dependency_overrides.clear()
        await cleanup_tenants([tenant_id])


@pytest.mark.integration
async def test_due_subscription_window() -> None:
    from app.modules.opportunities.subscriptions import due_subscription_ids

    tenant_id, user_id, _ = await seed_tenant_user("sub-due")
    now = datetime.now(UTC)
    try:
        async with SessionLocal() as session:
            due = OpportunitySubscription(
                tenant_id=tenant_id,
                user_id=user_id,
                name="到期",
                query_json={},
                interval_minutes=30,
                enabled=True,
                last_run_at=now - timedelta(minutes=45),
            )
            pending = OpportunitySubscription(
                tenant_id=tenant_id,
                user_id=user_id,
                name="未到",
                query_json={},
                interval_minutes=120,
                enabled=True,
                last_run_at=now - timedelta(minutes=10),
            )
            disabled = OpportunitySubscription(
                tenant_id=tenant_id,
                user_id=user_id,
                name="停用",
                query_json={},
                interval_minutes=30,
                enabled=False,
                last_run_at=None,
            )
            session.add_all([due, pending, disabled])
            await session.commit()
            ids = await due_subscription_ids(session, now=now)
            assert ids == [due.id]
    finally:
        await cleanup_tenants([tenant_id])


@pytest.mark.integration
async def test_manual_run_survives_bookkeeping_failure(monkeypatch) -> None:
    """记账失败不得让手动触发的同步 500——用户重试会对同一付费查询二次计费。"""
    tenant_id, _, email = await seed_tenant_user("sub-bookkeeping")
    adapter = SubscriptionAdapter()
    app.dependency_overrides[get_keeprisk_adapter] = lambda: adapter

    async def _explode(*args: object, **kwargs: object) -> None:
        raise RuntimeError("cost ledger unavailable")

    monkeypatch.setattr("app.modules.opportunities.subscriptions.record_gateway_call", _explode)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = await login(client, email)
            created = await client.post(
                "/api/v1/opportunity-subscriptions",
                json={"name": "记账失败", "query": {"keywords": ["政务云"]}},
                headers=headers,
            )
            subscription_id = created.json()["id"]
            run = await client.post(
                f"/api/v1/opportunity-subscriptions/{subscription_id}/run", headers=headers
            )
            assert run.status_code == 200, run.text
            assert run.json()["status"] == "succeeded"
            assert run.json()["rows_new"] == 3

        async with SessionLocal() as session:
            ledger = (
                await session.execute(
                    select(CostLedger).where(
                        CostLedger.tenant_id == tenant_id,
                        CostLedger.operation == "opportunity.list",
                    )
                )
            ).scalars().all()
            assert ledger == []
    finally:
        app.dependency_overrides.clear()
        await cleanup_tenants([tenant_id])
