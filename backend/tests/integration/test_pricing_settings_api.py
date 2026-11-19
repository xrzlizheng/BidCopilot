import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db.session import SessionLocal
from app.main import app
from app.modules.audit.models import AuditEvent
from tests.support import cleanup_tenants, login, seed_tenant_user

pytestmark = pytest.mark.integration

SETTINGS_URL = "/api/v1/system/pricing-settings"

COST_MODEL = {
    "direct_rate": "0.55",
    "indirect_rate": "0.12",
    "tax_rate": "0.06",
    "labor_rates": {"工程师": "1200"},
}
MARGINS = {"conservative": "0.15", "balanced": "0.10", "aggressive": "0.05"}
DEFAULT_MARGINS = {
    "conservative": "0.120000",
    "balanced": "0.080000",
    "aggressive": "0.040000",
}


async def test_pricing_settings_configure_then_strategy_tier_and_quote() -> None:
    tenant_id, _, email = await seed_tenant_user("pricing-settings")
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = await login(client, email)

            initial = await client.get(SETTINGS_URL, headers=headers)
            assert initial.status_code == 200, initial.text
            body = initial.json()
            assert body["cost_model"] == {
                "direct_rate": None,
                "indirect_rate": None,
                "tax_rate": None,
                "labor_rates": {},
            }
            assert body["target_margins"] == DEFAULT_MARGINS
            assert body["source"] == {
                "cost_model": "missing",
                "target_margins": "tool-default",
            }

            saved = await client.put(
                SETTINGS_URL,
                json={"cost_model": COST_MODEL, "target_margins": MARGINS},
                headers=headers,
            )
            assert saved.status_code == 200, saved.text
            saved_body = saved.json()
            assert saved_body["cost_model"] == {
                "direct_rate": "0.550000",
                "indirect_rate": "0.120000",
                "tax_rate": "0.060000",
                "labor_rates": {"工程师": "1200.00"},
            }
            assert saved_body["target_margins"] == {
                "conservative": "0.150000",
                "balanced": "0.100000",
                "aggressive": "0.050000",
            }
            assert saved_body["source"] == {
                "cost_model": "tenant-setting:pricing_cost_model",
                "target_margins": "tenant-setting:pricing_target_margins",
            }

            created = await client.post(
                "/api/v1/projects", json={"name": "报价配置集成项目"}, headers=headers
            )
            assert created.status_code == 201, created.text
            project_id = created.json()["id"]

            strategy_response = await client.post(
                f"/api/v1/projects/{project_id}/pricing/strategy",
                json={
                    "inputs": {
                        "quote_amount": "500000",
                        "other_direct_cost": "20000",
                        "labor_items": [{"role": "工程师", "days": "30"}],
                        "include_narrative": False,
                    }
                },
                headers=headers,
            )
            assert strategy_response.status_code == 200, strategy_response.text
            strategy = strategy_response.json()
            assert strategy["tiers"]
            balanced = next(t for t in strategy["tiers"] if t["tier"] == "balanced")
            assert balanced["status"] == "ok"

            quote_response = await client.post(
                f"/api/v1/projects/{project_id}/pricing/quote", json={}, headers=headers
            )
            assert quote_response.status_code == 200, quote_response.text
            quote = quote_response.json()
            assert quote["price_source"] == "tier:balanced"
            assert quote["total_amount"] == balanced["price"]

            invalid = await client.put(
                SETTINGS_URL,
                json={
                    "cost_model": {
                        **COST_MODEL,
                        "indirect_rate": "0.9",
                        "tax_rate": "0.2",
                    }
                },
                headers=headers,
            )
            assert invalid.status_code == 422, invalid.text
            assert invalid.json()["code"] == "validation_error"
            assert invalid.json()["message"]

            _, _, engineer_email = await seed_tenant_user(
                "pricing-settings-engineer", role_code="bid_engineer", tenant_id=tenant_id
            )
            engineer_headers = await login(client, engineer_email)
            forbidden = await client.put(
                SETTINGS_URL, json={"cost_model": COST_MODEL}, headers=engineer_headers
            )
            assert forbidden.status_code == 403, forbidden.text

            persisted = await client.get(SETTINGS_URL, headers=headers)
            assert persisted.status_code == 200, persisted.text
            assert persisted.json() == saved_body

        async with SessionLocal() as session:
            events = list(
                (
                    await session.execute(
                        select(AuditEvent).where(
                            AuditEvent.tenant_id == tenant_id,
                            AuditEvent.action == "pricing_settings.update",
                        )
                    )
                )
                .scalars()
            )
        assert len(events) == 1
        assert events[0].resource_type == "tenant_settings"
        assert events[0].resource_id == "pricing-settings"
        assert events[0].data["cost_model"] == saved_body["cost_model"]
        assert events[0].data["target_margins"] == saved_body["target_margins"]
    finally:
        await cleanup_tenants([tenant_id])


async def test_pricing_settings_tenant_isolation_and_partial_update() -> None:
    tenant_a, _, email_a = await seed_tenant_user("pricing-settings-a")
    tenant_b, _, email_b = await seed_tenant_user("pricing-settings-b")
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers_a = await login(client, email_a)
            headers_b = await login(client, email_b)

            saved = await client.put(
                SETTINGS_URL, json={"cost_model": COST_MODEL}, headers=headers_a
            )
            assert saved.status_code == 200, saved.text
            # 省略 target_margins 块：保持工具默认，不写租户键。
            assert saved.json()["target_margins"] == DEFAULT_MARGINS
            assert saved.json()["source"]["target_margins"] == "tool-default"

            other = await client.get(SETTINGS_URL, headers=headers_b)
            assert other.status_code == 200, other.text
            assert other.json()["source"]["cost_model"] == "missing"
            assert other.json()["cost_model"]["labor_rates"] == {}

            updated = await client.put(
                SETTINGS_URL, json={"target_margins": MARGINS}, headers=headers_a
            )
            assert updated.status_code == 200, updated.text
            assert updated.json()["cost_model"] == saved.json()["cost_model"]
            assert updated.json()["source"]["target_margins"] == (
                "tenant-setting:pricing_target_margins"
            )
    finally:
        await cleanup_tenants([tenant_a, tenant_b])