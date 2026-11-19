from datetime import UTC, datetime
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient

from app.db.session import SessionLocal
from app.main import app
from app.modules.opportunities.models import Opportunity
from tests.support import cleanup_tenants, login, seed_tenant_user


def _opportunity(tenant_id, *, title: str, buyer: str, region: str) -> Opportunity:
    return Opportunity(
        tenant_id=tenant_id,
        source="keeprisk",
        dedup_key=f"test-{tenant_id}-{title}",
        title=title,
        buyer=buyer,
        region=region,
        industry=["政务"],
        budget_amount=Decimal("1000000"),
        publish_at=datetime.now(UTC),
        info_type="1",
        detail_status="list_only",
        fetched_at=datetime.now(UTC),
    )


@pytest.mark.integration
async def test_buyer_watchlist_explanations_and_coverage() -> None:
    tenant_id, admin_id, email = await seed_tenant_user("buyer-watch")
    try:
        async with SessionLocal() as session:
            session.add_all(
                [
                    _opportunity(
                        tenant_id,
                        title="政务云系统集成项目",
                        buyer="某市大数据局",
                        region="广东-深圳",
                    ),
                    _opportunity(
                        tenant_id,
                        title="园区绿化养护服务",
                        buyer="某区园林局",
                        region="江苏-南京",
                    ),
                ]
            )
            await session.commit()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = await login(client, email)
            subscription = await client.post(
                "/api/v1/opportunity-subscriptions",
                json={
                    "name": "系统集成订阅",
                    "query": {"keywords": ["系统集成"], "region_list": [{"province": "广东"}]},
                },
                headers=headers,
            )
            assert subscription.status_code == 200, subscription.text

            listed = await client.get("/api/v1/opportunities", headers=headers)
            assert listed.status_code == 200, listed.text
            items = listed.json()["items"]
            assert len(items) == 2
            explained = next(item for item in items if "系统集成" in item["title"])
            assert explained["explanation"]["reasons"], explained
            assert explained["explanation"]["buyer_watched"] is False

            # [P1-4] 验证只有 opportunity:read 的标书工程师无法增删关注采购人（403）
            _, _, engineer_email = await seed_tenant_user(
                "eng-watch", role_code="bid_engineer", tenant_id=tenant_id
            )
            eng_headers = await login(client, engineer_email)
            forbidden = await client.post(
                "/api/v1/opportunities/buyer-watchlist",
                json={"buyer_name": "某市大数据局", "note": "重点客户"},
                headers=eng_headers,
            )
            assert forbidden.status_code == 403, forbidden.text

            watched = await client.post(
                "/api/v1/opportunities/buyer-watchlist",
                json={"buyer_name": "某市大数据局", "note": "重点客户"},
                headers=headers,
            )
            assert watched.status_code == 201, watched.text
            watch_id = watched.json()["id"]

            del_forbidden = await client.delete(
                f"/api/v1/opportunities/buyer-watchlist/{watch_id}",
                headers=eng_headers,
            )
            assert del_forbidden.status_code == 403, del_forbidden.text

            duplicate = await client.post(
                "/api/v1/opportunities/buyer-watchlist",
                json={"buyer_name": "某市大数据局"},
                headers=headers,
            )
            assert duplicate.status_code == 409, duplicate.text

            filtered = await client.get(
                "/api/v1/opportunities?buyer_watch=true", headers=headers
            )
            assert filtered.status_code == 200, filtered.text
            assert len(filtered.json()["items"]) == 1
            assert filtered.json()["items"][0]["explanation"]["buyer_watched"] is True

            coverage = await client.get("/api/v1/opportunities/coverage", headers=headers)
            assert coverage.status_code == 200, coverage.text
            body = coverage.json()
            assert body["total_opportunities"] == 2
            assert body["active_subscriptions"] == 1
            assert body["items"][0]["matched"] >= 1
            assert body["items"][0]["zero_hit"] is False

            removed = await client.delete(
                f"/api/v1/opportunities/buyer-watchlist/{watch_id}", headers=headers
            )
            assert removed.status_code == 204, removed.text
            remaining = await client.get(
                "/api/v1/opportunities/buyer-watchlist", headers=headers
            )
            assert remaining.json() == []
    finally:
        await cleanup_tenants([tenant_id])
