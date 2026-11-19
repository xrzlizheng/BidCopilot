from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.core.security import hash_password
from app.db.session import SessionLocal
from app.main import app
from app.modules.audit.models import AuditEvent
from app.modules.costs.models import CostLedger
from app.modules.gateway.deps import get_web_search_client
from app.modules.gateway.web_search import WebSearchItem, WebSearchPage
from app.modules.identity.models import Role, Tenant, User, UserRole
from app.modules.identity.roles import SYSTEM_ROLES


class FakeSearchClient:
    provider = "deepseek"

    def configured(self) -> bool:
        return True

    async def search(self, query: str, *, count: int | None = None) -> WebSearchPage:
        return WebSearchPage(
            provider=self.provider,
            query=query,
            status="ok",
            items=[
                WebSearchItem(
                    title="搜索结果",
                    url="https://example.test/result",
                    snippet="来自真实提供者的摘要",
                    published_at="2026-10-22",
                )
            ],
            raw_ref="local://web-search/test.json",
            fetched_at=datetime.now(UTC),
        )


async def _seed_tenant() -> tuple[UUID, str, list[UUID]]:
    async with SessionLocal() as session:
        role = (
            await session.execute(
                select(Role).where(Role.tenant_id.is_(None), Role.code == "tenant_admin")
            )
        ).scalar_one_or_none()
        if role is None:
            role = Role(
                tenant_id=None,
                code="tenant_admin",
                name="企业管理员",
                permissions=list(SYSTEM_ROLES["tenant_admin"]["permissions"]),
                is_system=True,
            )
            session.add(role)
            await session.flush()
        slug = f"web-search-{uuid4().hex[:10]}"
        tenant = Tenant(name="搜索测试", slug=slug, kind="customer")
        session.add(tenant)
        await session.flush()
        email = f"{slug}@example.com"
        user = User(
            tenant_id=tenant.id,
            email=email,
            password_hash=hash_password("Password123!"),
            display_name="搜索测试",
        )
        session.add(user)
        await session.flush()
        session.add(UserRole(tenant_id=tenant.id, user_id=user.id, role_id=role.id, scope_type="tenant", scope_id=tenant.id))
        await session.commit()
        return tenant.id, email, [tenant.id]


@pytest.mark.integration
async def test_web_search_api_records_cost_and_audit() -> None:
    tenant_id, email, tenant_ids = await _seed_tenant()
    app.dependency_overrides[get_web_search_client] = lambda: FakeSearchClient()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            login = await client.post(
                "/api/v1/auth/login",
                json={"email": email, "password": "Password123!"},
            )
            assert login.status_code == 200, login.text
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
            response = await client.post(
                "/api/v1/web-search",
                json={"query": "政务云 双活架构", "count": 3},
                headers=headers,
            )
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["status"] == "ok"
            assert payload["results"][0]["url"] == "https://example.test/result"

            async with SessionLocal() as session:
                cost = (
                    await session.execute(
                        select(CostLedger).where(CostLedger.tenant_id == tenant_id)
                    )
                ).scalars().one()
                assert cost.pcode == "web_search:deepseek"
                assert cost.operation == "web.search"
                assert cost.status == "ok"
                assert cost.rows == 1
                audit = (
                    await session.execute(
                        select(AuditEvent).where(
                            AuditEvent.tenant_id == tenant_id,
                            AuditEvent.action == "research.web_search",
                        )
                    )
                ).scalars().one()
                assert audit.data["provider"] == "deepseek"
    finally:
        app.dependency_overrides.pop(get_web_search_client, None)
        async with SessionLocal() as session:
            await session.execute(delete(CostLedger).where(CostLedger.tenant_id.in_(tenant_ids)))
            await session.execute(delete(AuditEvent).where(AuditEvent.tenant_id.in_(tenant_ids)))
            await session.execute(delete(Tenant).where(Tenant.id.in_(tenant_ids)))
            await session.commit()
