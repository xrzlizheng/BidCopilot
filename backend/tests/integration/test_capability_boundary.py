from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from app.core.security import hash_password
from app.db.session import SessionLocal
from app.main import app
from app.modules.audit.models import AuditEvent
from app.modules.identity.models import Role, Tenant, User, UserRole


async def _seed_user(label: str, permissions: list[str]) -> tuple[UUID, str]:
    async with SessionLocal() as session:
        slug = f"{label}-{uuid4().hex[:10]}"
        tenant = Tenant(name=label, slug=slug, kind="customer")
        session.add(tenant)
        await session.flush()
        role = Role(
            tenant_id=tenant.id,
            code=f"{label}_{uuid4().hex[:6]}",
            name=label,
            permissions=permissions,
            is_system=False,
        )
        session.add(role)
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


async def _login(client: AsyncClient, email: str) -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": "Password123!"}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def _cleanup(tenant_id: UUID) -> None:
    async with SessionLocal() as session:
        await session.execute(delete(AuditEvent).where(AuditEvent.tenant_id == tenant_id))
        await session.execute(delete(Tenant).where(Tenant.id == tenant_id))
        await session.commit()


@pytest.mark.integration
async def test_project_read_only_cannot_read_knowledge() -> None:
    tenant_id, email = await _seed_user("project-only", ["project:read"])
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = await _login(client, email)
            assert (
                await client.get("/api/v1/knowledge/documents", headers=headers)
            ).status_code == 403
            assert (
                await client.get("/api/v1/knowledge/search", params={"q": "test"}, headers=headers)
            ).status_code == 403
            assert (
                await client.get("/api/v1/knowledge-packs", headers=headers)
            ).status_code == 403
            assert (await client.get("/api/v1/skills", headers=headers)).status_code == 403
    finally:
        await _cleanup(tenant_id)


@pytest.mark.integration
async def test_knowledge_reader_can_read_and_see_capabilities() -> None:
    tenant_id, email = await _seed_user(
        "knowledge-reader", ["knowledge:read", "knowledge:use"]
    )
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = await _login(client, email)
            documents = await client.get("/api/v1/knowledge/documents", headers=headers)
            assert documents.status_code == 200
            capabilities = await client.get("/api/v1/auth/capabilities", headers=headers)
            assert capabilities.status_code == 200
            assert "knowledge:read" in capabilities.json()["permissions"]
    finally:
        await _cleanup(tenant_id)
