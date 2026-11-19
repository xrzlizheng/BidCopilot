from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.core.security import hash_password
from app.db.session import SessionLocal
from app.main import app
from app.modules.audit.models import AuditEvent
from app.modules.compliance.registry import load_rule_set
from app.modules.identity.models import Role, Tenant, User, UserRole
from app.modules.identity.roles import SYSTEM_ROLES


async def _seed_tenant(label: str) -> tuple[UUID, str]:
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
        session.add(UserRole(tenant_id=tenant.id, user_id=user.id, role_id=role.id, scope_type="tenant", scope_id=tenant.id))
        await session.commit()
        return tenant.id, email


async def _cleanup(tenant_ids: list[UUID]) -> None:
    async with SessionLocal() as session:
        await session.execute(delete(AuditEvent).where(AuditEvent.tenant_id.in_(tenant_ids)))
        await session.execute(delete(Tenant).where(Tenant.id.in_(tenant_ids)))
        await session.commit()


@pytest.mark.integration
async def test_compliance_policy_api_and_tenant_isolation() -> None:
    tenant_id, email = await _seed_tenant("policies-a")
    other_tenant_id, other_email = await _seed_tenant("policies-b")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            login = await client.post(
                "/api/v1/auth/login", json={"email": email, "password": "Password123!"}
            )
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
            policies = await client.get("/api/v1/policies", headers=headers)
            assert policies.status_code == 200, policies.text
            assert len(policies.json()) == len(load_rule_set()[1])
            assert all(item["enabled"] is True for item in policies.json())

            updated = await client.patch(
                "/api/v1/policies/CONTENT-EMPTY-001",
                json={"enabled": False, "severity_override": None},
                headers=headers,
            )
            assert updated.status_code == 200, updated.text
            assert updated.json()["enabled"] is False

            overridden = await client.patch(
                "/api/v1/policies/QUAL-UNKNOWN-001",
                json={"enabled": True, "severity_override": "low"},
                headers=headers,
            )
            assert overridden.status_code == 200, overridden.text
            assert overridden.json()["effective_severity"] == "low"

            missing = await client.patch(
                "/api/v1/policies/NOT-A-RULE",
                json={"enabled": True},
                headers=headers,
            )
            assert missing.status_code == 404

            other_login = await client.post(
                "/api/v1/auth/login", json={"email": other_email, "password": "Password123!"}
            )
            other_headers = {"Authorization": f"Bearer {other_login.json()['access_token']}"}
            other_policies = await client.get("/api/v1/policies", headers=other_headers)
            assert all(item["enabled"] is True for item in other_policies.json())
    finally:
        await _cleanup([tenant_id, other_tenant_id])
