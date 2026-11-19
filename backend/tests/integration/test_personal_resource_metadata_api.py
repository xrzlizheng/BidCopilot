import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from tests.support import cleanup_tenants, login, sample_tender_docx, seed_tenant_user


@pytest.mark.integration
async def test_admin_sees_only_personal_resource_metadata() -> None:
    tenant_id, _, admin_email = await seed_tenant_user("meta-admin")
    _, _, owner_email = await seed_tenant_user("meta-owner", "bid_engineer", tenant_id)
    _, _, compliance_email = await seed_tenant_user(
        "meta-compliance", "compliance", tenant_id
    )
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            admin = await login(client, admin_email)
            owner = await login(client, owner_email)
            compliance = await login(client, compliance_email)
            engineer = await login(client, owner_email)

            kb = await client.post(
                "/api/v1/knowledge/bases",
                json={"name": "个人治理库", "scope": "personal"},
                headers=owner,
            )
            assert kb.status_code == 201, kb.text
            kb_id = kb.json()["id"]
            upload = await client.post(
                "/api/v1/knowledge/documents",
                files={
                    "file": (
                        "private.docx",
                        sample_tender_docx(),
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    )
                },
                data={"title": "不会出现在元数据里的标题", "kb_id": kb_id},
                headers=owner,
            )
            assert upload.status_code == 200, upload.text

            pack = await client.post(
                "/api/v1/knowledge-packs",
                json={
                    "name": "个人治理模板",
                    "category": "structure",
                    "scope": "personal",
                    "content_json": {
                        "sections": [
                            {
                                "title": "章节",
                                "purpose": "测试",
                                "guidance": "测试",
                                "required_materials": ["材料"],
                            }
                        ]
                    },
                },
                headers=owner,
            )
            assert pack.status_code == 201, pack.text

            metadata = await client.get(
                "/api/v1/compliance/personal-resources", headers=admin
            )
            assert metadata.status_code == 200, metadata.text
            rows = metadata.json()
            kb_row = next(row for row in rows if row["id"] == kb_id)
            assert kb_row["resource_type"] == "knowledge_base"
            assert kb_row["document_count"] == 1
            assert kb_row["size_bytes"] > 0
            assert "content" not in kb_row
            assert "documents" not in kb_row
            assert all("title" not in row for row in rows)
            pack_row = next(row for row in rows if row["id"] == pack.json()["id"])
            assert pack_row["resource_type"] == "knowledge_pack"
            assert pack_row["version"]
            assert "content_json" not in pack_row

            compliance_view = await client.get(
                "/api/v1/compliance/personal-resources", headers=compliance
            )
            assert compliance_view.status_code == 200

            denied = await client.get(
                "/api/v1/compliance/personal-resources", headers=engineer
            )
            assert denied.status_code == 403
    finally:
        await cleanup_tenants([tenant_id])
