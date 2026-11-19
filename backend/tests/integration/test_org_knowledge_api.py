import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from tests.support import cleanup_tenants, login, sample_tender_docx, seed_tenant_user


async def _upload_doc(
    client: AsyncClient, headers: dict[str, str], kb_id: str
) -> dict:
    response = await client.post(
        "/api/v1/knowledge/documents",
        files={
            "file": (
                "dept.docx",
                sample_tender_docx(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
        data={"title": "部门投标资料", "kind": "solution", "kb_id": kb_id},
        headers=headers,
    )
    return {"status": response.status_code, "body": response.json() if response.content else {}}


@pytest.mark.integration
async def test_department_tree_and_knowledge_isolation() -> None:
    tenant_id, admin_id, admin_email = await seed_tenant_user("org-admin")
    _, manager_id, manager_email = await seed_tenant_user(
        "org-manager", "bid_engineer", tenant_id
    )
    _, member_id, member_email = await seed_tenant_user(
        "org-member", "bid_engineer", tenant_id
    )
    _, outsider_id, outsider_email = await seed_tenant_user(
        "org-outsider", "bid_engineer", tenant_id
    )
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            admin = await login(client, admin_email)
            manager = await login(client, manager_email)
            member = await login(client, member_email)
            outsider = await login(client, outsider_email)

            org = await client.post(
                "/api/v1/org-units",
                json={"name": "投标一部", "kind": "department"},
                headers=admin,
            )
            assert org.status_code == 201, org.text
            org_id = org.json()["id"]

            for user_id, role in ((manager_id, "manager"), (member_id, "member")):
                added = await client.post(
                    f"/api/v1/org-units/{org_id}/members",
                    json={"user_id": str(user_id), "member_role": role},
                    headers=admin,
                )
                assert added.status_code == 201, added.text

            tree = await client.get("/api/v1/org-units/tree", headers=manager)
            assert tree.status_code == 200
            assert any(node["id"] == org_id for node in tree.json())

            dept_kb = await client.post(
                "/api/v1/knowledge/bases",
                json={
                    "name": "投标一部资料库",
                    "scope": "org_unit",
                    "org_unit_id": org_id,
                },
                headers=manager,
            )
            assert dept_kb.status_code == 201, dept_kb.text
            kb_id = dept_kb.json()["id"]

            manager_upload = await _upload_doc(client, manager, kb_id)
            assert manager_upload["status"] == 200, manager_upload

            member_docs = await client.get(
                "/api/v1/knowledge/documents", params={"kb_id": kb_id}, headers=member
            )
            assert member_docs.status_code == 200
            assert member_docs.json()

            member_upload = await _upload_doc(client, member, kb_id)
            assert member_upload["status"] == 403

            outsider_bases = await client.get("/api/v1/knowledge/bases", headers=outsider)
            assert outsider_bases.status_code == 200
            assert kb_id not in {row["id"] for row in outsider_bases.json()}

            outsider_detail = await client.get(
                f"/api/v1/knowledge/bases/{kb_id}", headers=outsider
            )
            assert outsider_detail.status_code == 404

            outsider_search = await client.get(
                "/api/v1/knowledge/search", params={"q": "营业执照"}, headers=outsider
            )
            assert outsider_search.status_code == 200
            assert outsider_search.json()["chunks"] == []

            shared = await client.post(
                "/api/v1/knowledge/bases",
                json={"name": "跨部门共享库", "scope": "tenant", "visibility": "shared"},
                headers=admin,
            )
            assert shared.status_code == 201, shared.text
            shared_id = shared.json()["id"]
            granted = await client.post(
                f"/api/v1/knowledge/bases/{shared_id}/acl",
                json={"subject_type": "org_unit", "subject_id": org_id, "role": "viewer"},
                headers=admin,
            )
            assert granted.status_code == 201, granted.text

            member_shared = await client.get(
                "/api/v1/knowledge/bases", headers=member
            )
            assert shared_id in {row["id"] for row in member_shared.json()}
            outsider_shared = await client.get(
                "/api/v1/knowledge/bases", headers=outsider
            )
            assert shared_id not in {row["id"] for row in outsider_shared.json()}
    finally:
        await cleanup_tenants([tenant_id])
