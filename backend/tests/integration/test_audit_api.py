from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.core.security import hash_password
from app.db.session import SessionLocal
from app.main import app
from app.modules.audit.models import AuditEvent
from app.modules.audit.service import AuditService
from app.modules.identity.models import Role, Tenant, User, UserRole
from app.modules.identity.roles import SYSTEM_ROLES


async def _seed_tenant(label: str) -> tuple[UUID, str, UUID]:
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
        return tenant.id, email, user.id


async def _cleanup(tenant_id: UUID) -> None:
    async with SessionLocal() as session:
        await session.execute(delete(AuditEvent).where(AuditEvent.tenant_id == tenant_id))
        await session.execute(delete(Tenant).where(Tenant.id == tenant_id))
        await session.commit()


@pytest.mark.integration
async def test_audit_events_filters_and_chain_verification() -> None:
    tenant_id, email, user_id = await _seed_tenant("audit-a")
    try:
        async with SessionLocal() as session:
            service = AuditService(session)
            for index in range(3):
                await service.append(
                    tenant_id=tenant_id,
                    actor_id=user_id,
                    action="test.append",
                    resource_type="test",
                    resource_id=str(index),
                    data={"index": index},
                )
            await session.commit()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            login = await client.post(
                "/api/v1/auth/login", json={"email": email, "password": "Password123!"}
            )
            assert login.status_code == 200, login.text
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

            events = await client.get("/api/v1/audit/events", headers=headers)
            assert events.status_code == 200, events.text
            assert events.json()["total"] == 4
            assert events.json()["items"][0]["action"] == "auth.login"

            filtered = await client.get(
                "/api/v1/audit/events?action=test.append&resource_type=test",
                headers=headers,
            )
            assert filtered.status_code == 200, filtered.text
            assert filtered.json()["total"] == 3
            assert {item["action"] for item in filtered.json()["items"]} == {"test.append"}

            verified = await client.get("/api/v1/audit/verify", headers=headers)
            assert verified.status_code == 200, verified.text
            assert verified.json() == {"valid": True, "events": 4}

        async with SessionLocal() as session:
            event = (
                await session.execute(
                    select(AuditEvent)
                    .where(AuditEvent.tenant_id == tenant_id)
                    .order_by(AuditEvent.seq.desc())
                    .limit(1)
                )
            ).scalar_one()
            event.data = {"tampered": True}
            await session.commit()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            login = await client.post(
                "/api/v1/auth/login", json={"email": email, "password": "Password123!"}
            )
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
            tampered = await client.get("/api/v1/audit/verify", headers=headers)
            assert tampered.status_code == 200, tampered.text
            assert tampered.json()["valid"] is False
    finally:
        await _cleanup(tenant_id)
