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
from app.modules.notifications.models import Notification


async def _seed_admin(label: str) -> tuple[UUID, str]:
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
        email = f"admin-{slug}@example.com"
        user = User(
            tenant_id=tenant.id,
            email=email,
            password_hash=hash_password("Password123!"),
            display_name="Admin",
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
        await session.execute(delete(Notification).where(Notification.tenant_id == tenant_id))
        await session.execute(delete(AuditEvent).where(AuditEvent.tenant_id == tenant_id))
        await session.execute(delete(Tenant).where(Tenant.id == tenant_id))
        await session.commit()


@pytest.mark.integration
async def test_notification_channel_crud() -> None:
    tenant_id, email = await _seed_admin("notify-channels")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            login = await client.post(
                "/api/v1/auth/login", json={"email": email, "password": "Password123!"}
            )
            assert login.status_code == 200, login.text
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

            empty = await client.get("/api/v1/notifications/channels", headers=headers)
            assert empty.status_code == 200, empty.text
            assert empty.json() == []

            insecure = await client.post(
                "/api/v1/notifications/channels",
                json={"kind": "wechat", "target": "http://example.com/hook"},
                headers=headers,
            )
            assert insecure.status_code == 422, insecure.text

            wechat = await client.post(
                "/api/v1/notifications/channels",
                json={
                    "kind": "wechat",
                    "target": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test",
                    "label": "投标群",
                },
                headers=headers,
            )
            assert wechat.status_code == 201, wechat.text
            wechat_id = wechat.json()["id"]
            assert wechat.json()["enabled"] is True

            duplicate = await client.post(
                "/api/v1/notifications/channels",
                json={
                    "kind": "wechat",
                    "target": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test",
                },
                headers=headers,
            )
            assert duplicate.status_code == 409, duplicate.text

            mail = await client.post(
                "/api/v1/notifications/channels",
                json={"kind": "email", "target": "a@example.com,b@example.com"},
                headers=headers,
            )
            assert mail.status_code == 201, mail.text

            listed = await client.get("/api/v1/notifications/channels", headers=headers)
            assert listed.status_code == 200, listed.text
            assert {row["kind"] for row in listed.json()} == {"wechat", "email"}

            removed = await client.delete(
                f"/api/v1/notifications/channels/{wechat_id}", headers=headers
            )
            assert removed.status_code == 204, removed.text

            remaining = await client.get("/api/v1/notifications/channels", headers=headers)
            assert [row["kind"] for row in remaining.json()] == ["email"]
    finally:
        await _cleanup(tenant_id)
