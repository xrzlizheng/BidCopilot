from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db.session import SessionLocal
from app.main import app
from app.modules.identity.models import User
from tests.support import cleanup_tenants, login, sample_tender_docx, seed_tenant_user


async def _upload_doc(
    client: AsyncClient,
    headers: dict[str, str],
    kb_id: str,
    title: str = "测试知识文档",
) -> dict:
    response = await client.post(
        "/api/v1/knowledge/documents",
        files={
            "file": (
                "knowledge.docx",
                sample_tender_docx(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
        data={"title": title, "kind": "policy", "kb_id": kb_id},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _set_clearance(user_id: UUID, clearance: str) -> None:
    async with SessionLocal() as session:
        user = (
            await session.execute(select(User).where(User.id == user_id))
        ).scalar_one()
        user.clearance = clearance
        await session.commit()


@pytest.mark.integration
async def test_personal_kb_is_private_to_owner() -> None:
    tenant_id, admin_id, admin_email = await seed_tenant_user("kb-acl-admin")
    _, engineer_id, engineer_email = await seed_tenant_user(
        "kb-acl-engineer", "bid_engineer", tenant_id
    )
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            admin = await login(client, admin_email)
            engineer = await login(client, engineer_email)
            created = await client.post(
                "/api/v1/knowledge/bases",
                json={"name": "工程师个人库", "scope": "personal"},
                headers=engineer,
            )
            assert created.status_code == 201, created.text
            personal_kb_id = created.json()["id"]
            await _upload_doc(client, engineer, personal_kb_id, "个人方案资料")

            admin_bases = await client.get("/api/v1/knowledge/bases", headers=admin)
            assert admin_bases.status_code == 200
            assert personal_kb_id not in {row["id"] for row in admin_bases.json()}

            denied = await client.get(
                f"/api/v1/knowledge/bases/{personal_kb_id}", headers=admin
            )
            assert denied.status_code == 404

            admin_search = await client.get(
                "/api/v1/knowledge/search", params={"q": "营业执照"}, headers=admin
            )
            assert admin_search.status_code == 200
            assert all(
                hit["title"] != "个人方案资料" for hit in admin_search.json()["chunks"]
            )

            engineer_search = await client.get(
                "/api/v1/knowledge/search", params={"q": "营业执照"}, headers=engineer
            )
            assert engineer_search.status_code == 200
            assert engineer_search.json()["chunks"]
    finally:
        await cleanup_tenants([tenant_id])


@pytest.mark.integration
async def test_shared_kb_requires_acl_and_grants_read_access() -> None:
    tenant_id, admin_id, admin_email = await seed_tenant_user("kb-shared-admin")
    _, engineer_id, engineer_email = await seed_tenant_user(
        "kb-shared-engineer", "bid_engineer", tenant_id
    )
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            admin = await login(client, admin_email)
            engineer = await login(client, engineer_email)
            created = await client.post(
                "/api/v1/knowledge/bases",
                json={"name": "仅授权库", "scope": "tenant", "visibility": "shared"},
                headers=admin,
            )
            assert created.status_code == 201, created.text
            kb_id = created.json()["id"]
            await _upload_doc(client, admin, kb_id, "仅授权可见资料")

            before = await client.get(
                "/api/v1/knowledge/documents", params={"kb_id": kb_id}, headers=engineer
            )
            assert before.status_code == 200
            assert before.json() == []

            granted = await client.post(
                f"/api/v1/knowledge/bases/{kb_id}/acl",
                json={"subject_type": "user", "subject_id": str(engineer_id), "role": "viewer"},
                headers=admin,
            )
            assert granted.status_code == 201, granted.text

            after = await client.get(
                "/api/v1/knowledge/documents", params={"kb_id": kb_id}, headers=engineer
            )
            assert after.status_code == 200
            assert after.json()

            search = await client.get(
                "/api/v1/knowledge/search", params={"q": "营业执照"}, headers=engineer
            )
            assert search.status_code == 200
            assert search.json()["chunks"]
    finally:
        await cleanup_tenants([tenant_id])


@pytest.mark.integration
async def test_document_acl_can_tighten_enterprise_kb() -> None:
    tenant_id, admin_id, admin_email = await seed_tenant_user("kb-doc-acl-admin")
    _, engineer_id, engineer_email = await seed_tenant_user(
        "kb-doc-acl-engineer", "bid_engineer", tenant_id
    )
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            admin = await login(client, admin_email)
            engineer = await login(client, engineer_email)
            bases = await client.get("/api/v1/knowledge/bases", headers=admin)
            default_kb = next(
                row for row in bases.json() if row["scope"] == "tenant" and row["visibility"] == "enterprise"
            )
            doc = await _upload_doc(client, admin, default_kb["id"], "企业默认库资料")

            before = await client.get(
                f"/api/v1/knowledge/documents/{doc['id']}", headers=engineer
            )
            assert before.status_code == 200

            tightened = await client.post(
                f"/api/v1/knowledge/documents/{doc['id']}/acl",
                json={"subject_type": "user", "subject_id": str(admin_id), "role": "viewer"},
                headers=admin,
            )
            assert tightened.status_code == 201, tightened.text

            denied = await client.get(
                f"/api/v1/knowledge/documents/{doc['id']}", headers=engineer
            )
            assert denied.status_code == 403
            denied_search = await client.get(
                "/api/v1/knowledge/search", params={"q": "营业执照"}, headers=engineer
            )
            assert denied_search.status_code == 200
            assert all(
                hit["document_id"] != doc["id"] for hit in denied_search.json()["chunks"]
            )

            reopened = await client.post(
                f"/api/v1/knowledge/documents/{doc['id']}/acl",
                json={"subject_type": "user", "subject_id": str(engineer_id), "role": "viewer"},
                headers=admin,
            )
            assert reopened.status_code == 201, reopened.text
            allowed = await client.get(
                f"/api/v1/knowledge/documents/{doc['id']}", headers=engineer
            )
            assert allowed.status_code == 200
    finally:
        await cleanup_tenants([tenant_id])


@pytest.mark.integration
async def test_compliance_kb_texts_respect_document_acl() -> None:
    tenant_id, admin_id, admin_email = await seed_tenant_user("kb-compliance-admin")
    _, engineer_id, _ = await seed_tenant_user(
        "kb-compliance-engineer", "bid_engineer", tenant_id
    )
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            admin = await login(client, admin_email)
            bases = await client.get("/api/v1/knowledge/bases", headers=admin)
            default_kb = next(
                row
                for row in bases.json()
                if row["scope"] == "tenant" and row["visibility"] == "enterprise"
            )
            doc = await _upload_doc(client, admin, default_kb["id"], "合规相似度专用资料")
            tightened = await client.post(
                f"/api/v1/knowledge/documents/{doc['id']}/acl",
                json={
                    "subject_type": "user",
                    "subject_id": str(admin_id),
                    "role": "viewer",
                },
                headers=admin,
            )
            assert tightened.status_code == 201, tightened.text

            from app.modules.compliance.service import load_accessible_kb_texts
            from app.modules.identity.service import load_user_context
            from app.modules.knowledge.models import KnowledgeChunk

            async with SessionLocal() as session:
                chunk_text = (
                    await session.execute(
                        select(KnowledgeChunk.text).where(
                            KnowledgeChunk.tenant_id == tenant_id,
                            KnowledgeChunk.document_id == UUID(doc["id"]),
                        )
                    )
                ).scalars().first()
                assert chunk_text
                admin_user = await load_user_context(session, admin_id)
                engineer_user = await load_user_context(session, engineer_id)
                assert admin_user is not None and engineer_user is not None
                admin_texts = await load_accessible_kb_texts(
                    session, tenant_id, admin_user
                )
                engineer_texts = await load_accessible_kb_texts(
                    session, tenant_id, engineer_user
                )

        assert chunk_text in admin_texts
        assert chunk_text not in engineer_texts, "无权文档不得进入合规相似度语料"
    finally:
        await cleanup_tenants([tenant_id])


@pytest.mark.integration
async def test_classification_hides_confidential_kb() -> None:
    tenant_id, admin_id, admin_email = await seed_tenant_user("kb-class-admin")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            admin = await login(client, admin_email)
            await _set_clearance(admin_id, "confidential")
            created = await client.post(
                "/api/v1/knowledge/bases",
                json={
                    "name": "机密投标库",
                    "scope": "tenant",
                    "visibility": "enterprise",
                    "classification": "confidential",
                },
                headers=admin,
            )
            assert created.status_code == 201, created.text
            kb_id = created.json()["id"]

            await _set_clearance(admin_id, "internal")
            denied = await client.get(f"/api/v1/knowledge/bases/{kb_id}", headers=admin)
            assert denied.status_code == 403
            bases = await client.get("/api/v1/knowledge/bases", headers=admin)
            assert kb_id not in {row["id"] for row in bases.json()}

            await _set_clearance(admin_id, "confidential")
            allowed = await client.get(f"/api/v1/knowledge/bases/{kb_id}", headers=admin)
            assert allowed.status_code == 200
    finally:
        await cleanup_tenants([tenant_id])
