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
from app.modules.identity.models import Role, Tenant, User, UserRole
from app.modules.identity.roles import SYSTEM_ROLES
from app.modules.opportunities.models import Opportunity
from app.modules.projects.models import BidProject, BidResult, TenderConstraint, TenderDocument
from app.modules.system.models import TenantSetting

pytestmark = pytest.mark.integration

D = Decimal


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
            UserRole(
                tenant_id=tenant.id,
                user_id=user.id,
                role_id=roles["tenant_admin"].id,
                scope_type="tenant",
                scope_id=tenant.id,
            )
        )
        await session.commit()
        return tenant.id, email


async def _login(client: AsyncClient, email: str) -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": "Password123!"}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def _cleanup(tenant_ids: list[UUID]) -> None:
    async with SessionLocal() as session:
        await session.execute(delete(AuditEvent).where(AuditEvent.tenant_id.in_(tenant_ids)))
        await session.execute(delete(Tenant).where(Tenant.id.in_(tenant_ids)))
        await session.commit()


async def _seed_pricing_history(tenant_id: UUID, project_id: UUID) -> None:
    """真实落库的同行业/同预算区间历史样本：6 条中标 + 3 条报价失标。"""
    async with SessionLocal() as session:
        setting = TenantSetting(
            tenant_id=tenant_id,
            key="pricing_cost_model",
            value={
                "direct_rate": "0.55",
                "indirect_rate": "0.12",
                "tax_rate": "0.06",
                "labor_rates": {"工程师": "1200"},
            },
        )
        session.add(setting)

        current = await session.get(BidProject, project_id)
        assert current is not None
        current_opp = Opportunity(
            tenant_id=tenant_id,
            source="test",
            dedup_key=uuid4().hex,
            title="当前报价项目",
            industry=["软件和信息技术服务业"],
            budget_amount=D("52000"),
            info_type="tender",
            fetched_at=datetime.now(UTC),
        )
        session.add(current_opp)
        await session.flush()
        current.opportunity_id = current_opp.id

        document = TenderDocument(
            tenant_id=tenant_id,
            project_id=project_id,
            filename="tender.pdf",
            size_bytes=1,
            file_hash="a" * 64,
            object_ref="local://tender.pdf",
            parse_status="parsed",
        )
        session.add(document)
        await session.flush()
        session.add(
            TenderConstraint(
                tenant_id=tenant_id,
                project_id=project_id,
                document_id=document.id,
                version=1,
                payload={"budget": {"amount": "52000", "currency": "CNY"}},
                parse_meta={},
            )
        )

        ratios = ["0.80", "0.85", "0.88", "0.90", "0.95", "0.92"]
        for index, ratio in enumerate(ratios):
            opportunity = Opportunity(
                tenant_id=tenant_id,
                source="test",
                dedup_key=uuid4().hex,
                title=f"历史中标项目 {index}",
                industry=["软件和信息技术服务业"],
                budget_amount=D("52000"),
                win_bid_amount=D("52000") * D(ratio),
                info_type="award",
                fetched_at=datetime.now(UTC),
            )
            session.add(opportunity)
            project = BidProject(tenant_id=tenant_id, name=f"历史中标项目 {index}", status="won")
            session.add(project)
            await session.flush()
            project.opportunity_id = opportunity.id
            session.add(
                BidResult(
                    tenant_id=tenant_id,
                    project_id=project.id,
                    outcome="won",
                    win_amount=D("52000") * D(ratio),
                )
            )
        for index in range(3):
            opportunity = Opportunity(
                tenant_id=tenant_id,
                source="test",
                dedup_key=uuid4().hex,
                title=f"历史报价失标项目 {index}",
                industry=["软件和信息技术服务业"],
                budget_amount=D("52000"),
                info_type="award",
                fetched_at=datetime.now(UTC),
            )
            session.add(opportunity)
            project = BidProject(tenant_id=tenant_id, name=f"历史报价失标项目 {index}", status="lost")
            session.add(project)
            await session.flush()
            project.opportunity_id = opportunity.id
            session.add(
                BidResult(
                    tenant_id=tenant_id,
                    project_id=project.id,
                    outcome="lost",
                    loss_reason="price",
                )
            )
        await session.commit()


@pytest.mark.integration
async def test_pricing_strategy_happy_path_with_real_history() -> None:
    tenant_id, email = await _seed_tenant("pricing-happy")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = await _login(client, email)
            created = await client.post(
                "/api/v1/projects", json={"name": "报价策略集成测试项目"}, headers=headers
            )
            assert created.status_code == 201, created.text
            project_id = created.json()["id"]
            await _seed_pricing_history(tenant_id, UUID(project_id))

            generated = await client.post(
                f"/api/v1/projects/{project_id}/pricing/strategy",
                json={
                    "inputs": {
                        "quote_amount": "50000",
                        "other_direct_cost": "0",
                        "labor_items": [{"role": "工程师", "days": "20"}],
                    }
                },
                headers=headers,
            )
            assert generated.status_code == 200, generated.text
            body = generated.json()
            assert body["status"] == "ok", body
            assert body["engine_version"] == "pricing-v1"
            # 绝对成本口径：人力 20 天 x 1200 = 24000 + 其他直接 0
            # + 间接费 50000*0.12=6000 + 税费 50000*0.06=3000 => 33000
            assert body["cost"]["labor_cost"] == "24000.00"
            assert body["cost"]["indirect_cost"] == "6000.00"
            assert body["cost"]["tax_cost"] == "3000.00"
            assert body["cost"]["total_cost"] == "33000.00"
            assert body["price_band"]["status"] == "ok"
            assert body["price_band"]["p50"] is not None
            assert len(body["tiers"]) == 3
            assert len(body["sensitivity"]["rows"]) == 7
            assert body["narrative"]["source"] == "template"
            assert "非中标承诺" in body["disclaimer"]

            latest = await client.get(
                f"/api/v1/projects/{project_id}/pricing/strategy/latest", headers=headers
            )
            assert latest.status_code == 200, latest.text
            assert latest.json()["id"] == body["id"]

            band = await client.get(
                f"/api/v1/projects/{project_id}/pricing/price-band", headers=headers
            )
            assert band.status_code == 200, band.text
            assert band.json()["price_band"]["sample_count"] >= 5

            sensitivity = await client.get(
                f"/api/v1/projects/{project_id}/pricing/sensitivity", headers=headers
            )
            assert sensitivity.status_code == 200, sensitivity.text
            assert sensitivity.json()["status"] == "ok"
    finally:
        await _cleanup([tenant_id])


@pytest.mark.integration
async def test_pricing_insufficient_data_and_cross_tenant_404() -> None:
    tenant_id, email = await _seed_tenant("pricing-insufficient")
    other_tenant_id, other_email = await _seed_tenant("pricing-other")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = await _login(client, email)
            other_headers = await _login(client, other_email)
            created = await client.post(
                "/api/v1/projects", json={"name": "无数据报价项目"}, headers=headers
            )
            assert created.status_code == 201, created.text
            project_id = created.json()["id"]

            generated = await client.post(
                f"/api/v1/projects/{project_id}/pricing/strategy",
                json={"inputs": {}},
                headers=headers,
            )
            assert generated.status_code == 200, generated.text
            body = generated.json()
            assert body["status"] == "insufficient_data"
            assert body["missing"]
            assert body["price_band"]["status"] == "insufficient_data"
            assert body["tiers"] == []
            assert "未使用默认值" in body["narrative"]["text"]

            # 越租户按项目 ID 读取必须 404，不泄露项目存在性。
            for method, path in (
                ("GET", f"/api/v1/projects/{project_id}/pricing/strategy/latest"),
                ("GET", f"/api/v1/projects/{project_id}/pricing/price-band"),
                ("GET", f"/api/v1/projects/{project_id}/pricing/sensitivity"),
            ):
                response = await client.request(method, path, headers=other_headers)
                assert response.status_code == 404, response.text
            cross_post = await client.post(
                f"/api/v1/projects/{project_id}/pricing/strategy",
                json={"inputs": {}},
                headers=other_headers,
            )
            assert cross_post.status_code == 404, cross_post.text
    finally:
        await _cleanup([tenant_id, other_tenant_id])
