from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from tests.support import cleanup_tenants, login, seed_tenant_user


@pytest.mark.integration
async def test_custom_role_crud_and_assignment() -> None:
    tenant_id, _, admin_email = await seed_tenant_user("role-admin")
    _, _, engineer_email = await seed_tenant_user("role-engineer", "bid_engineer", tenant_id)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            admin = await login(client, admin_email)
            engineer = await login(client, engineer_email)

            created = await client.post(
                "/api/v1/roles",
                json={"name": "知识只读", "permissions": ["knowledge:read"]},
                headers=admin,
            )
            assert created.status_code == 201, created.text
            role = created.json()
            assert role["is_system"] is False
            role_id = role["id"]

            roles = await client.get("/api/v1/roles", headers=admin)
            assert roles.status_code == 200
            assert any(
                row["code"] == role["code"] and row["id"] == role_id for row in roles.json()
            )

            email = f"custom-{uuid4().hex[:8]}@example.com"
            user = await client.post(
                "/api/v1/users",
                json={
                    "email": email,
                    "password": "Password123!",
                    "display_name": "自定义角色用户",
                    "role_codes": [role["code"]],
                },
                headers=admin,
            )
            assert user.status_code == 201, user.text
            assert role["code"] in user.json()["roles"]

            custom_user = await login(client, email)
            documents = await client.get(
                "/api/v1/knowledge/documents", headers=custom_user
            )
            assert documents.status_code == 200
            packs = await client.get("/api/v1/knowledge-packs", headers=custom_user)
            assert packs.status_code == 403

            denied = await client.post(
                "/api/v1/roles",
                json={"name": "越权角色", "permissions": ["knowledge:read"]},
                headers=engineer,
            )
            assert denied.status_code == 403

            escalation = await client.post(
                "/api/v1/roles",
                json={"name": "通配越权", "permissions": ["*"]},
                headers=admin,
            )
            assert escalation.status_code == 422

            assigned_delete = await client.delete(
                f"/api/v1/roles/{role_id}", headers=admin
            )
            assert assigned_delete.status_code == 409

            extra = await client.post(
                "/api/v1/roles",
                json={"name": "未分配角色", "permissions": ["asset:read"]},
                headers=admin,
            )
            assert extra.status_code == 201, extra.text
            deleted = await client.delete(
                f"/api/v1/roles/{extra.json()['id']}", headers=admin
            )
            assert deleted.status_code == 204
    finally:
        await cleanup_tenants([tenant_id])
