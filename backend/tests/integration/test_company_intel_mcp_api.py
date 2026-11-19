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
from app.modules.gateway.contract import DataResult, SourceStatus
from app.modules.gateway.deps import get_enterprise_intel_adapter
from app.modules.gateway.enterprise_adapter import EnterpriseIntelAdapter
from app.modules.identity.models import Role, Tenant, User, UserRole
from app.modules.identity.roles import SYSTEM_ROLES


class FakeEnterpriseIntelAdapter(EnterpriseIntelAdapter):
    def __init__(self) -> None:
        self.profile_calls = 0

    async def basic_profile(self, ent_name: str) -> DataResult[dict]:
        self.profile_calls += 1
        return DataResult(
            status=SourceStatus.OK,
            data={"entName": ent_name, "creditCode": "914403001922038216"},
            source="keeprisk-mcp",
            fetched_at=datetime.now(UTC),
            order_no="mcp-order-1",
            charged=True,
            api_calls=4,
            billing_units=2,
        )

    async def batch_verify(self, ent_names: list[str]) -> DataResult[list[dict]]:
        rows = [{"entName": name} for name in ent_names if name != "未匹配企业"]
        return DataResult(
            status=SourceStatus.OK if rows else SourceStatus.EMPTY,
            data=rows,
            source="keeprisk-mcp",
            fetched_at=datetime.now(UTC),
            order_no="mcp-batch-1",
            charged=True,
        )

    async def compare(self, ent_names: list[str]) -> DataResult[dict]:
        return DataResult(
            status=SourceStatus.OK,
            data={
                "ranking": [{"entity": ent_names[0], "rank": 1, "score": 93.7}],
                "companies": [{"entity": ent_names[0]}],
                "count": len(ent_names),
            },
            source="keeprisk-mcp",
            fetched_at=datetime.now(UTC),
            charged=True,
            api_calls=12,
            billing_units=12,
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


async def _cleanup(tenant_id: UUID) -> None:
    async with SessionLocal() as session:
        await session.execute(delete(CostLedger).where(CostLedger.tenant_id == tenant_id))
        await session.execute(delete(AuditEvent).where(AuditEvent.tenant_id == tenant_id))
        await session.execute(delete(Tenant).where(Tenant.id == tenant_id))
        await session.commit()


@pytest.mark.integration
async def test_company_intel_mcp_profile_records_ledger_and_audit() -> None:
    tenant_id, email = await _seed_tenant("company-intel-mcp")
    fake = FakeEnterpriseIntelAdapter()
    app.dependency_overrides[get_enterprise_intel_adapter] = lambda: fake
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            login = await client.post(
                "/api/v1/auth/login", json={"email": email, "password": "Password123!"}
            )
            assert login.status_code == 200, login.text
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

            response = await client.get(
                "/api/v1/company-intel/profile",
                params={"ent_name": "华为技术有限公司"},
                headers=headers,
            )
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["status"] == "ok"
            assert body["data"]["entName"] == "华为技术有限公司"
            assert body["order_no"] == "mcp-order-1"
            assert body["charged"] is True
            assert body["api_calls"] == 4
            assert body["billing_units"] == 2
            assert fake.profile_calls == 1

        async with SessionLocal() as session:
            ledger = (
                await session.execute(
                    select(CostLedger).where(
                        CostLedger.tenant_id == tenant_id,
                        CostLedger.pcode == "mcp:enterprise_basic_profile",
                    )
                )
            ).scalar_one()
            assert ledger.operation == "company_intel.profile"
            assert ledger.rows == 4
            assert ledger.charged is True
            assert ledger.order_no == "mcp-order-1"

            audit = (
                await session.execute(
                    select(AuditEvent).where(
                        AuditEvent.tenant_id == tenant_id,
                        AuditEvent.action == "company_intel.profile",
                    )
                )
            ).scalar_one()
            assert audit.resource_id == "华为技术有限公司"
    finally:
        app.dependency_overrides.pop(get_enterprise_intel_adapter, None)
        await _cleanup(tenant_id)


@pytest.mark.integration
async def test_company_intel_mcp_batch_records_ledger() -> None:
    tenant_id, email = await _seed_tenant("company-intel-batch")
    fake = FakeEnterpriseIntelAdapter()
    app.dependency_overrides[get_enterprise_intel_adapter] = lambda: fake
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            login = await client.post(
                "/api/v1/auth/login", json={"email": email, "password": "Password123!"}
            )
            assert login.status_code == 200, login.text
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

            response = await client.post(
                "/api/v1/company-intel/batch",
                json={"ent_names": ["华为技术有限公司", "未匹配企业"]},
                headers=headers,
            )
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["status"] == "ok"
            assert body["data"] == [{"entName": "华为技术有限公司"}]
            assert body["charged"] is True
            assert body["order_no"] == "mcp-batch-1"

        async with SessionLocal() as session:
            ledger = (
                await session.execute(
                    select(CostLedger).where(
                        CostLedger.tenant_id == tenant_id,
                        CostLedger.pcode == "mcp:batch_query_companies",
                    )
                )
            ).scalar_one()
            assert ledger.operation == "company_intel.batch"
            assert ledger.rows == 1
            assert ledger.charged is True
    finally:
        app.dependency_overrides.pop(get_enterprise_intel_adapter, None)
        await _cleanup(tenant_id)


@pytest.mark.integration
async def test_company_intel_mcp_compare_records_ledger() -> None:
    tenant_id, email = await _seed_tenant("company-intel-compare")
    fake = FakeEnterpriseIntelAdapter()
    app.dependency_overrides[get_enterprise_intel_adapter] = lambda: fake
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            login = await client.post(
                "/api/v1/auth/login", json={"email": email, "password": "Password123!"}
            )
            assert login.status_code == 200, login.text
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

            response = await client.post(
                "/api/v1/company-intel/compare",
                json={"ent_names": ["华为技术有限公司", "中兴通讯股份有限公司"]},
                headers=headers,
            )
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["status"] == "ok"
            assert body["data"]["ranking"][0]["entity"] == "华为技术有限公司"
            assert body["api_calls"] == 12
            assert body["billing_units"] == 12

        async with SessionLocal() as session:
            ledger = (
                await session.execute(
                    select(CostLedger).where(
                        CostLedger.tenant_id == tenant_id,
                        CostLedger.pcode == "mcp:compare_enterprises",
                    )
                )
            ).scalar_one()
            assert ledger.operation == "company_intel.compare"
            assert ledger.rows == 12
            assert ledger.charged is True
    finally:
        app.dependency_overrides.pop(get_enterprise_intel_adapter, None)
        await _cleanup(tenant_id)
