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


async def _seed_tenant(label: str) -> tuple[UUID, str, UUID]:
    async with SessionLocal() as session:
        admin_role = (
            await session.execute(
                select(Role).where(Role.tenant_id.is_(None), Role.code == "tenant_admin")
            )
        ).scalar_one_or_none()
        if admin_role is None:
            admin_role = Role(
                tenant_id=None,
                code="tenant_admin",
                name="企业管理员",
                permissions=list(SYSTEM_ROLES["tenant_admin"]["permissions"]),
                is_system=True,
            )
            session.add(admin_role)
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
        session.add(UserRole(tenant_id=tenant.id, user_id=user.id, role_id=admin_role.id, scope_type="tenant", scope_id=tenant.id))
        await session.commit()
        return tenant.id, email, user.id


async def _cleanup(tenant_ids: list[UUID]) -> None:
    async with SessionLocal() as session:
        await session.execute(delete(AuditEvent).where(AuditEvent.tenant_id.in_(tenant_ids)))
        await session.execute(delete(Tenant).where(Tenant.id.in_(tenant_ids)))
        await session.commit()


@pytest.mark.integration
async def test_tenant_user_management_and_guards() -> None:
    tenant_id, admin_email, admin_id = await _seed_tenant("users-a")
    other_tenant_id, other_email, _ = await _seed_tenant("users-b")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            login = await client.post(
                "/api/v1/auth/login", json={"email": admin_email, "password": "Password123!"}
            )
            assert login.status_code == 200, login.text
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

            roles = await client.get("/api/v1/roles", headers=headers)
            assert roles.status_code == 200, roles.text
            codes = {role["code"] for role in roles.json()}
            assert {"tenant_admin", "bid_manager", "bid_engineer", "compliance"} <= codes
            assert "platform_admin" not in codes

            created = await client.post(
                "/api/v1/users",
                json={
                    "email": "engineer@example.com",
                    "password": "Engineer123!",
                    "display_name": "标书工程师",
                    "role_codes": ["bid_engineer"],
                },
                headers=headers,
            )
            assert created.status_code == 201, created.text
            user_id = created.json()["id"]
            assert created.json()["roles"] == ["bid_engineer"]

            login_user = await client.post(
                "/api/v1/auth/login",
                json={"email": "engineer@example.com", "password": "Engineer123!"},
            )
            assert login_user.status_code == 200, login_user.text
            user_headers = {"Authorization": f"Bearer {login_user.json()['access_token']}"}
            me = await client.get("/api/v1/auth/me", headers=user_headers)
            assert me.json()["roles"] == ["bid_engineer"]
            forbidden = await client.get("/api/v1/users", headers=user_headers)
            assert forbidden.status_code == 403

            updated = await client.patch(
                f"/api/v1/users/{user_id}",
                json={"display_name": "高级标书工程师", "role_codes": ["bid_manager"]},
                headers=headers,
            )
            assert updated.status_code == 200, updated.text
            assert updated.json()["roles"] == ["bid_manager"]
            assert updated.json()["display_name"] == "高级标书工程师"

            reset = await client.patch(
                f"/api/v1/users/{user_id}",
                json={"password": "NewPassword123!"},
                headers=headers,
            )
            assert reset.status_code == 200, reset.text
            revoked = await client.get("/api/v1/auth/me", headers=user_headers)
            assert revoked.status_code == 401
            revoked_refresh = await client.post("/api/v1/auth/refresh")
            assert revoked_refresh.status_code == 401
            old_login = await client.post(
                "/api/v1/auth/login",
                json={"email": "engineer@example.com", "password": "Engineer123!"},
            )
            assert old_login.status_code == 401
            new_login = await client.post(
                "/api/v1/auth/login",
                json={"email": "engineer@example.com", "password": "NewPassword123!"},
            )
            assert new_login.status_code == 200, new_login.text

            disabled = await client.patch(
                f"/api/v1/users/{user_id}", json={"status": "disabled"}, headers=headers
            )
            assert disabled.status_code == 200, disabled.text
            disabled_login = await client.post(
                "/api/v1/auth/login",
                json={"email": "engineer@example.com", "password": "NewPassword123!"},
            )
            assert disabled_login.status_code == 401

            self_disable = await client.patch(
                f"/api/v1/users/{admin_id}", json={"status": "disabled"}, headers=headers
            )
            assert self_disable.status_code == 422
            self_roles = await client.patch(
                f"/api/v1/users/{admin_id}",
                json={"role_codes": ["bid_manager"]},
                headers=headers,
            )
            assert self_roles.status_code == 422

            duplicate = await client.post(
                "/api/v1/users",
                json={
                    "email": "engineer@example.com",
                    "password": "Another123!",
                    "display_name": "重复用户",
                    "role_codes": ["bid_engineer"],
                },
                headers=headers,
            )
            assert duplicate.status_code == 409

            users = await client.get("/api/v1/users", headers=headers)
            assert users.status_code == 200, users.text
            assert {user["email"] for user in users.json()} == {admin_email, "engineer@example.com"}

            other_login = await client.post(
                "/api/v1/auth/login", json={"email": other_email, "password": "Password123!"}
            )
            other_headers = {"Authorization": f"Bearer {other_login.json()['access_token']}"}
            other_list = await client.get("/api/v1/users", headers=other_headers)
            assert other_list.status_code == 200
            assert {user["email"] for user in other_list.json()} == {other_email}
            cross = await client.patch(
                f"/api/v1/users/{user_id}", json={"display_name": "越权"}, headers=other_headers
            )
            assert cross.status_code == 404

            change = await client.post(
                "/api/v1/auth/change-password",
                json={"current_password": "Password123!", "new_password": "AdminNew123!"},
                headers=headers,
            )
            assert change.status_code == 204
            revoked_admin = await client.get("/api/v1/auth/me", headers=headers)
            assert revoked_admin.status_code == 401
            new_admin_login = await client.post(
                "/api/v1/auth/login",
                json={"email": admin_email, "password": "AdminNew123!"},
            )
            assert new_admin_login.status_code == 200, new_admin_login.text

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
            assert {"user.create", "user.update"} <= actions
    finally:
        await _cleanup([tenant_id, other_tenant_id])


@pytest.mark.integration
async def test_user_manage_role_cannot_escalate_to_tenant_admin() -> None:
    """自定义 user:manage 角色不得借创建/改密把自己提成租户管理员。"""
    tenant_id, _, _ = await _seed_tenant("users-escalate")
    try:
        async with SessionLocal() as session:
            role = Role(
                tenant_id=tenant_id,
                code="hr",
                name="人事",
                permissions=["user:manage", "role:read"],
                is_system=False,
                scope_types=["tenant"],
            )
            session.add(role)
            await session.flush()
            hr_email = f"hr-{uuid4().hex[:8]}@example.com"
            hr = User(
                tenant_id=tenant_id,
                email=hr_email,
                password_hash=hash_password("Password123!"),
                display_name="人事",
            )
            session.add(hr)
            await session.flush()
            session.add(
                UserRole(
                    tenant_id=tenant_id,
                    user_id=hr.id,
                    role_id=role.id,
                    scope_type="tenant",
                    scope_id=tenant_id,
                )
            )
            await session.commit()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            login_response = await client.post(
                "/api/v1/auth/login",
                json={"email": hr_email, "password": "Password123!"},
            )
            assert login_response.status_code == 200, login_response.text
            headers = {"Authorization": f"Bearer {login_response.json()['access_token']}"}

            created = await client.post(
                "/api/v1/users",
                json={
                    "email": f"esc-{uuid4().hex[:8]}@example.com",
                    "password": "Password123!",
                    "display_name": "越权账号",
                    "role_codes": ["tenant_admin"],
                },
                headers=headers,
            )
            assert created.status_code == 403, created.text
    finally:
        await _cleanup([tenant_id])
