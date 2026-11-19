from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db.session import SessionLocal
from app.main import app
from app.modules.compliance.models import ComplianceAccessRequest
from tests.support import cleanup_tenants, login, sample_tender_docx, seed_tenant_user


async def _upload_doc(
    client: AsyncClient, headers: dict[str, str], kb_id: str
) -> dict:
    response = await client.post(
        "/api/v1/knowledge/documents",
        files={
            "file": (
                "private.docx",
                sample_tender_docx(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
        data={"title": "个人私有资料", "kind": "solution", "kb_id": kb_id},
        headers=headers,
    )
    return {"status": response.status_code, "body": response.json() if response.content else {}}


@pytest.mark.integration
async def test_break_glass_requires_two_person_approval_and_is_read_only() -> None:
    tenant_id, _, admin_email = await seed_tenant_user("bg-admin")
    _, _, owner_email = await seed_tenant_user("bg-owner", "bid_engineer", tenant_id)
    _, _, compliance_email = await seed_tenant_user(
        "bg-compliance", "compliance", tenant_id
    )
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            admin = await login(client, admin_email)
            owner = await login(client, owner_email)
            compliance = await login(client, compliance_email)

            kb = await client.post(
                "/api/v1/knowledge/bases",
                json={"name": "个人私有库", "scope": "personal"},
                headers=owner,
            )
            assert kb.status_code == 201, kb.text
            kb_id = kb.json()["id"]
            uploaded = await _upload_doc(client, owner, kb_id)
            assert uploaded["status"] == 200, uploaded
            doc_id = uploaded["body"]["id"]

            before = await client.get(f"/api/v1/knowledge/bases/{kb_id}", headers=admin)
            assert before.status_code == 404

            requested = await client.post(
                "/api/v1/compliance/access-requests",
                json={
                    "resource_type": "knowledge_base",
                    "resource_id": kb_id,
                    "reason": "合规检查需要核对个人资料",
                    "requested_minutes": 30,
                },
                headers=admin,
            )
            assert requested.status_code == 201, requested.text
            request_id = requested.json()["id"]
            assert requested.json()["status"] == "pending"

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
            assert approved.json()["status"] == "approved"
            assert approved.json()["expires_at"]

            visible = await client.get(f"/api/v1/knowledge/bases/{kb_id}", headers=admin)
            assert visible.status_code == 200
            doc = await client.get(
                f"/api/v1/knowledge/documents/{doc_id}", headers=admin
            )
            assert doc.status_code == 200
            search = await client.get(
                "/api/v1/knowledge/search", params={"q": "营业执照"}, headers=admin
            )
            assert search.status_code == 200
            assert search.json()["chunks"]

            write_attempt = await _upload_doc(client, admin, kb_id)
            assert write_attempt["status"] == 404

            async with SessionLocal() as session:
                row = (
                    await session.execute(
                        select(ComplianceAccessRequest).where(
                            ComplianceAccessRequest.id == request_id
                        )
                    )
                ).scalar_one()
                row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
                await session.commit()

            expired = await client.get(f"/api/v1/knowledge/bases/{kb_id}", headers=admin)
            assert expired.status_code == 404
    finally:
        await cleanup_tenants([tenant_id])
