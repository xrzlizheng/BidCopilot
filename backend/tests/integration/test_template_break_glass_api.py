import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from tests.support import cleanup_tenants, login, seed_tenant_user

STRUCTURE = {
    "sections": [
        {
            "title": "个人方案",
            "purpose": "个人模板",
            "guidance": "测试 break-glass",
            "required_materials": ["方案"],
        }
    ]
}


@pytest.mark.integration
async def test_template_break_glass_is_read_only() -> None:
    tenant_id, _, admin_email = await seed_tenant_user("tpl-bg-admin")
    _, _, owner_email = await seed_tenant_user("tpl-bg-owner", "bid_engineer", tenant_id)
    _, _, compliance_email = await seed_tenant_user(
        "tpl-bg-compliance", "compliance", tenant_id
    )
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            admin = await login(client, admin_email)
            owner = await login(client, owner_email)
            compliance = await login(client, compliance_email)

            created = await client.post(
                "/api/v1/knowledge-packs",
                json={
                    "name": "个人私有模板",
                    "category": "structure",
                    "scope": "personal",
                    "content_json": STRUCTURE,
                },
                headers=owner,
            )
            assert created.status_code == 201, created.text
            pack_id = created.json()["id"]
            pack_code = created.json()["code"]

            before = await client.get("/api/v1/knowledge-packs", headers=admin)
            assert pack_code not in {row["code"] for row in before.json()}

            requested = await client.post(
                "/api/v1/compliance/access-requests",
                json={
                    "resource_type": "knowledge_pack",
                    "resource_id": pack_id,
                    "reason": "合规检查需要查看个人模板",
                    "requested_minutes": 30,
                },
                headers=admin,
            )
            assert requested.status_code == 201, requested.text
            request_id = requested.json()["id"]

            self_approve = await client.post(
                f"/api/v1/compliance/access-requests/{request_id}/approve",
                headers=admin,
            )
            assert self_approve.status_code == 403

            approved = await client.post(
                f"/api/v1/compliance/access-requests/{request_id}/approve",
                headers=compliance,
            )
            assert approved.status_code == 200, approved.text

            after = await client.get("/api/v1/knowledge-packs", headers=admin)
            assert pack_code in {row["code"] for row in after.json()}

            patch = await client.patch(
                f"/api/v1/knowledge-packs/{pack_id}",
                json={"name": "调阅方越权修改"},
                headers=admin,
            )
            assert patch.status_code == 404
    finally:
        await cleanup_tenants([tenant_id])
