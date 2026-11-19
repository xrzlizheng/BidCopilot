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
async def test_model_config_api_and_tenant_isolation() -> None:
    tenant_id, email = await _seed_tenant("models-a")
    other_tenant_id, other_email = await _seed_tenant("models-b")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            login = await client.post(
                "/api/v1/auth/login", json={"email": email, "password": "Password123!"}
            )
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
            configs = await client.get("/api/v1/system/model-configs", headers=headers)
            assert configs.status_code == 200, configs.text
            assert [row["purpose"] for row in configs.json()] == ["main", "judge"]
            assert all(row["configured"] is False for row in configs.json())

            updated = await client.patch(
                "/api/v1/system/model-configs/main",
                json={
                    "base_url": "http://127.0.0.1:9999/v1",
                    "model": "deepseek-flash",
                    "enabled": True,
                },
                headers=headers,
            )
            assert updated.status_code == 200, updated.text
            assert updated.json()["effective_model"] == "deepseek-flash"
            assert updated.json()["overridden"] is True

            status = await client.get("/api/v1/system/ai-status", headers=headers)
            assert status.status_code == 200
            assert status.json()["llm"]["model"] == "deepseek-flash"
            assert status.json()["llm"]["host"] == "127.0.0.1:9999"
            assert status.json()["web_search"]["provider"] == "deepseek"
            assert status.json()["web_search"]["configured"] is False

            invalid_url = await client.patch(
                "/api/v1/system/model-configs/judge",
                json={"base_url": "ftp://invalid", "model": "judge", "enabled": True},
                headers=headers,
            )
            assert invalid_url.status_code == 422

            other_login = await client.post(
                "/api/v1/auth/login", json={"email": other_email, "password": "Password123!"}
            )
            other_headers = {"Authorization": f"Bearer {other_login.json()['access_token']}"}
            other_configs = await client.get(
                "/api/v1/system/model-configs", headers=other_headers
            )
            assert all(row["configured"] is False for row in other_configs.json())
            assert all(row["overridden"] is False for row in other_configs.json())
    finally:
        await _cleanup([tenant_id, other_tenant_id])
