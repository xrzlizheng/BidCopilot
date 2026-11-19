from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient

from app.db.session import SessionLocal
from app.main import app
from app.modules.projects.models import ScoringItem, TenderConstraint, TenderDocument
from tests.support import cleanup_tenants, login, seed_tenant_user

STRUCTURE = {
    "sections": [
        {
            "title": "运维方案",
            "purpose": "响应运维要求",
            "guidance": "量化 SLA",
            "required_materials": ["服务承诺"],
        }
    ]
}


async def _create_pack(
    client: AsyncClient,
    headers: dict[str, str],
    name: str,
    *,
    scope: str | None = None,
) -> dict:
    payload: dict = {
        "name": name,
        "category": "structure",
        "content_json": STRUCTURE,
    }
    if scope is not None:
        payload["scope"] = scope
    response = await client.post("/api/v1/knowledge-packs", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


async def _seed_constraint(tenant_id: UUID, project_id: UUID) -> None:
    async with SessionLocal() as session:
        document = TenderDocument(
            tenant_id=tenant_id,
            project_id=project_id,
            filename="template-tender.docx",
            size_bytes=1024,
            file_hash="t" * 64,
            object_ref="local/template-tender.docx",
            parse_status="parsed",
            parsed_at=datetime.now(UTC),
        )
        session.add(document)
        await session.flush()
        constraint = TenderConstraint(
            tenant_id=tenant_id,
            project_id=project_id,
            document_id=document.id,
            version=1,
            payload={},
            parse_meta={},
        )
        session.add(constraint)
        await session.flush()
        session.add(
            ScoringItem(
                tenant_id=tenant_id,
                project_id=project_id,
                constraint_id=constraint.id,
                category="技术",
                item="技术方案",
                score=Decimal("30"),
                criteria="完整可行",
                source_span={},
                confidence="high",
            )
        )
        await session.commit()


@pytest.mark.integration
async def test_template_scope_isolation_and_publish_permission() -> None:
    tenant_id, _, admin_email = await seed_tenant_user("tpl-admin")
    _, _, engineer_email = await seed_tenant_user("tpl-engineer", "bid_engineer", tenant_id)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            admin = await login(client, admin_email)
            engineer = await login(client, engineer_email)

            tenant_pack = await _create_pack(
                client, admin, "企业模板", scope="tenant"
            )
            personal_pack = await _create_pack(
                client, admin, "管理员个人模板", scope="personal"
            )
            assert tenant_pack["scope"] == "tenant"
            assert personal_pack["scope"] == "personal"

            engineer_packs = await client.get("/api/v1/knowledge-packs", headers=engineer)
            assert engineer_packs.status_code == 200
            codes = {pack["code"] for pack in engineer_packs.json()}
            assert tenant_pack["code"] in codes
            assert personal_pack["code"] not in codes

            engineer_tenant_attempt = await client.post(
                "/api/v1/knowledge-packs",
                json={
                    "name": "越权企业模板",
                    "category": "structure",
                    "scope": "tenant",
                    "content_json": STRUCTURE,
                },
                headers=engineer,
            )
            assert engineer_tenant_attempt.status_code == 403

            engineer_personal = await _create_pack(client, engineer, "工程师个人模板")
            assert engineer_personal["scope"] == "personal"

            admin_packs = await client.get("/api/v1/knowledge-packs", headers=admin)
            admin_codes = {pack["code"] for pack in admin_packs.json()}
            assert engineer_personal["code"] not in admin_codes

            forbidden_patch = await client.patch(
                f"/api/v1/knowledge-packs/{personal_pack['id']}",
                json={"name": "被工程师改掉"},
                headers=engineer,
            )
            assert forbidden_patch.status_code == 404

            project = await client.post(
                "/api/v1/projects", json={"name": "模板隔离项目"}, headers=engineer
            )
            assert project.status_code == 201, project.text
            project_id = project.json()["id"]
            await _seed_constraint(tenant_id, UUID(project_id))

            tenant_apply = await client.put(
                f"/api/v1/projects/{project_id}/template",
                json={"template_code": tenant_pack["code"]},
                headers=engineer,
            )
            assert tenant_apply.status_code == 200, tenant_apply.text

            other_personal_apply = await client.put(
                f"/api/v1/projects/{project_id}/template",
                json={"template_code": personal_pack["code"]},
                headers=engineer,
            )
            assert other_personal_apply.status_code == 422

            own_personal_apply = await client.put(
                f"/api/v1/projects/{project_id}/template",
                json={"template_code": engineer_personal["code"]},
                headers=engineer,
            )
            assert own_personal_apply.status_code == 200, own_personal_apply.text
    finally:
        await cleanup_tenants([tenant_id])
