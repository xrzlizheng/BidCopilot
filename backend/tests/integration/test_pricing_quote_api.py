from decimal import Decimal
from io import BytesIO
from uuid import UUID
from zipfile import ZipFile

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db.session import SessionLocal
from app.main import app
from app.modules.audit.models import AuditEvent
from app.modules.system.models import TenantSetting
from tests.support import cleanup_tenants, login, seed_tenant_user

pytestmark = pytest.mark.integration

D = Decimal

COST_MODEL = {
    "direct_rate": "0.55",
    "indirect_rate": "0.12",
    "tax_rate": "0.06",
    "labor_rates": {"工程师": "1200"},
}


async def _seed_cost_model(tenant_id: UUID) -> None:
    async with SessionLocal() as session:
        session.add(
            TenantSetting(tenant_id=tenant_id, key="pricing_cost_model", value=COST_MODEL)
        )
        await session.commit()


async def _create_project(client: AsyncClient, headers: dict, name: str) -> str:
    created = await client.post("/api/v1/projects", json={"name": name}, headers=headers)
    assert created.status_code == 201, created.text
    return created.json()["id"]


async def _generate_strategy(
    client: AsyncClient, headers: dict, project_id: str, inputs: dict
) -> dict:
    response = await client.post(
        f"/api/v1/projects/{project_id}/pricing/strategy",
        json={"inputs": inputs},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_quote_generate_latest_export_and_audit() -> None:
    tenant_id, _, email = await seed_tenant_user("quote-happy")
    try:
        await _seed_cost_model(tenant_id)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = await login(client, email)
            project_id = await _create_project(client, headers, "报价单集成测试项目")
            strategy = await _generate_strategy(
                client,
                headers,
                project_id,
                {
                    "quote_amount": "50000",
                    "other_direct_cost": "0",
                    "labor_items": [{"role": "工程师", "days": "20"}],
                    "include_narrative": False,
                },
            )
            balanced = next(t for t in strategy["tiers"] if t["tier"] == "balanced")
            assert balanced["status"] == "ok"

            generated = await client.post(
                f"/api/v1/projects/{project_id}/pricing/quote", json={}, headers=headers
            )
            assert generated.status_code == 200, generated.text
            quote = generated.json()
            assert quote["total_amount"] == balanced["price"]
            assert quote["price_source"] == "tier:balanced"
            assert quote["tier"] == "balanced"
            assert quote["strategy_id"] == strategy["id"]
            assert quote["quote_number"].startswith("BJ-")
            assert quote["amount_in_words"].endswith("整") or "分" in quote["amount_in_words"]
            assert len(quote["items"]) == 1
            assert quote["items"][0]["amount"] == quote["total_amount"]
            assert quote["valid_until"] is not None

            latest = await client.get(
                f"/api/v1/projects/{project_id}/pricing/quote/latest", headers=headers
            )
            assert latest.status_code == 200, latest.text
            assert latest.json()["id"] == quote["id"]

            explicit = await client.post(
                f"/api/v1/projects/{project_id}/pricing/quote",
                json={
                    "quote_amount": "50000",
                    "items": [
                        {"name": "软件开发", "quantity": "1", "unit": "项", "unit_price": "30000"},
                        {"name": "运维服务", "spec": "一年", "quantity": "2", "unit": "年", "unit_price": "10000"},
                    ],
                    "notes": "含税总价",
                    "validity_days": 60,
                },
                headers=headers,
            )
            assert explicit.status_code == 200, explicit.text
            body = explicit.json()
            assert body["total_amount"] == "50000.00"
            assert body["price_source"] == "input"
            assert body["tier"] is None
            assert body["amount_in_words"] == "伍万元整"
            assert [item["amount"] for item in body["items"]] == ["30000.00", "20000.00"]

            exported = await client.post(
                f"/api/v1/projects/{project_id}/pricing/quote/export/docx", headers=headers
            )
            assert exported.status_code == 200, exported.text
            assert "wordprocessingml" in exported.headers["content-type"]
            assert f'filename="quote_{body["quote_number"]}.docx"' in exported.headers["content-disposition"]
            data = exported.content
            assert data[:2] == b"PK"
            with ZipFile(BytesIO(data)) as archive:
                xml = archive.read("word/document.xml").decode("utf-8")
            assert "伍万元整" in xml
            assert "软件开发" in xml
            assert "含税总价" in xml

            latest = await client.get(
                f"/api/v1/projects/{project_id}/pricing/quote/latest", headers=headers
            )
            assert latest.json()["id"] == body["id"]

        async with SessionLocal() as session:
            actions = set(
                (
                    await session.execute(
                        select(AuditEvent.action).where(AuditEvent.tenant_id == tenant_id)
                    )
                )
                .scalars()
                .all()
            )
        assert "project.pricing.quote.generate" in actions
        assert "project.pricing.quote.export" in actions
    finally:
        await cleanup_tenants([tenant_id])


async def test_quote_items_mismatch_and_missing_strategy() -> None:
    tenant_id, _, email = await seed_tenant_user("quote-invalid")
    try:
        await _seed_cost_model(tenant_id)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = await login(client, email)
            project_id = await _create_project(client, headers, "报价单校验项目")

            no_strategy = await client.post(
                f"/api/v1/projects/{project_id}/pricing/quote", json={}, headers=headers
            )
            assert no_strategy.status_code == 404, no_strategy.text
            no_quote = await client.get(
                f"/api/v1/projects/{project_id}/pricing/quote/latest", headers=headers
            )
            assert no_quote.status_code == 404
            no_export = await client.post(
                f"/api/v1/projects/{project_id}/pricing/quote/export/docx", headers=headers
            )
            assert no_export.status_code == 404

            await _generate_strategy(
                client,
                headers,
                project_id,
                {
                    "quote_amount": "50000",
                    "other_direct_cost": "0",
                    "labor_items": [{"role": "工程师", "days": "20"}],
                    "include_narrative": False,
                },
            )
            mismatch = await client.post(
                f"/api/v1/projects/{project_id}/pricing/quote",
                json={
                    "quote_amount": "50000",
                    "items": [
                        {"name": "软件开发", "quantity": "1", "unit": "项", "unit_price": "30000"}
                    ],
                },
                headers=headers,
            )
            assert mismatch.status_code == 422, mismatch.text
            assert "不一致" in mismatch.json()["message"]
    finally:
        await cleanup_tenants([tenant_id])


async def test_quote_unexecutable_tier_rejected_but_override_allowed() -> None:
    tenant_id, _, email = await seed_tenant_user("quote-floor")
    try:
        await _seed_cost_model(tenant_id)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = await login(client, email)
            project_id = await _create_project(client, headers, "预算低于成本底线项目")
            strategy = await _generate_strategy(
                client,
                headers,
                project_id,
                {
                    "quote_amount": "50000",
                    "budget_amount": "20000",
                    "other_direct_cost": "0",
                    "labor_items": [{"role": "工程师", "days": "20"}],
                    "include_narrative": False,
                },
            )
            assert all(t["status"] == "insufficient_data" for t in strategy["tiers"])

            rejected = await client.post(
                f"/api/v1/projects/{project_id}/pricing/quote",
                json={"tier": "balanced"},
                headers=headers,
            )
            assert rejected.status_code == 422, rejected.text
            assert "不可执行" in rejected.json()["message"]

            override = await client.post(
                f"/api/v1/projects/{project_id}/pricing/quote",
                json={"quote_amount": "20000"},
                headers=headers,
            )
            assert override.status_code == 200, override.text
            assert override.json()["total_amount"] == "20000.00"
            assert override.json()["price_source"] == "input"
    finally:
        await cleanup_tenants([tenant_id])


async def test_quote_cross_tenant_returns_404() -> None:
    tenant_id, _, email = await seed_tenant_user("quote-owner")
    other_tenant_id, _, other_email = await seed_tenant_user("quote-intruder")
    try:
        await _seed_cost_model(tenant_id)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = await login(client, email)
            other_headers = await login(client, other_email)
            project_id = await _create_project(client, headers, "跨租户报价项目")
            await _generate_strategy(
                client,
                headers,
                project_id,
                {
                    "quote_amount": "50000",
                    "other_direct_cost": "0",
                    "labor_items": [{"role": "工程师", "days": "20"}],
                    "include_narrative": False,
                },
            )
            created = await client.post(
                f"/api/v1/projects/{project_id}/pricing/quote", json={}, headers=headers
            )
            assert created.status_code == 200, created.text

            for method, path in (
                ("GET", f"/api/v1/projects/{project_id}/pricing/quote/latest"),
                ("POST", f"/api/v1/projects/{project_id}/pricing/quote"),
                ("POST", f"/api/v1/projects/{project_id}/pricing/quote/export/docx"),
            ):
                response = await client.request(method, path, json={}, headers=other_headers)
                assert response.status_code == 404, (method, path, response.text)
    finally:
        await cleanup_tenants([tenant_id, other_tenant_id])