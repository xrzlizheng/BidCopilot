import json
from datetime import UTC, datetime
from decimal import Decimal
from io import BytesIO
from uuid import UUID, uuid4

import pytest
from docx import Document
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.core.security import hash_password
from app.db.session import SessionLocal
from app.main import app
from app.modules.audit.models import AuditEvent
from app.modules.generation.deps import get_llm_client
from app.modules.identity.models import Role, Tenant, User, UserRole
from app.modules.identity.roles import SYSTEM_ROLES
from app.modules.projects.models import ScoringItem, TenderConstraint, TenderDocument


class CaptureLLM:
    model = "capture"

    def __init__(self) -> None:
        self.last_user: str | None = None

    def configured(self) -> bool:
        return True

    async def complete(self, *, system: str, user: str) -> str:
        self.last_user = user
        return json.dumps(
            {"blocks": [{"type": "paragraph", "text": "按模板要求生成的章节内容。"}]},
            ensure_ascii=False,
        )


async def _seed_tenant(label: str) -> tuple[UUID, str]:
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
        return tenant.id, email


async def _cleanup(tenant_ids: list[UUID]) -> None:
    async with SessionLocal() as session:
        await session.execute(delete(AuditEvent).where(AuditEvent.tenant_id.in_(tenant_ids)))
        await session.execute(delete(Tenant).where(Tenant.id.in_(tenant_ids)))
        await session.commit()


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
        session.add_all(
            [
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
                ),
                ScoringItem(
                    tenant_id=tenant_id,
                    project_id=project_id,
                    constraint_id=constraint.id,
                    category="商务",
                    item="项目业绩",
                    score=Decimal("20"),
                    criteria="同类合同",
                    source_span={},
                    confidence="high",
                ),
            ]
        )
        await session.commit()


@pytest.mark.integration
async def test_templates_materials_workspace_and_formatted_export() -> None:
    tenant_id, email = await _seed_tenant("templates-a")
    other_tenant_id, other_email = await _seed_tenant("templates-b")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            login = await client.post(
                "/api/v1/auth/login", json={"email": email, "password": "Password123!"}
            )
            assert login.status_code == 200, login.text
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

            company = await client.post(
                "/api/v1/companies",
                json={"name": "模板测试集成商"},
                headers=headers,
            )
            assert company.status_code == 201, company.text
            company_id = company.json()["id"]
            qualification = await client.post(
                "/api/v1/assets/qualifications",
                json={
                    "company_id": company_id,
                    "name": "信息系统集成及服务资质",
                    "level": "二级",
                    "valid_to": "2028-01-01",
                },
                headers=headers,
            )
            assert qualification.status_code == 201, qualification.text
            project = await client.post(
                "/api/v1/projects",
                json={
                    "name": "模板生产验收项目",
                    "company_id": company_id,
                    "bid_deadline": "2026-12-01T09:00:00Z",
                },
                headers=headers,
            )
            assert project.status_code == 201, project.text
            project_id = project.json()["id"]
            await _seed_constraint(tenant_id, UUID(project_id))

            packs = await client.get("/api/v1/knowledge-packs", headers=headers)
            assert packs.status_code == 200, packs.text
            codes = {pack["code"] for pack in packs.json()}
            assert {
                "builtin.gov_it",
                "builtin.gov_goods",
                "builtin.engineering",
                "builtin.format_gov",
            } <= codes
            skills = await client.get("/api/v1/skills", headers=headers)
            assert skills.status_code == 200
            assert any(
                skill["code"] == "similarity" and skill["status"] == "ready"
                for skill in skills.json()
            )

            custom = await client.post(
                "/api/v1/knowledge-packs",
                headers=headers,
                json={
                    "name": "自定义运维模板",
                    "category": "structure",
                    "description": "测试自定义模板",
                    "content_json": {
                        "sections": [
                            {
                                "title": "运维方案",
                                "purpose": "响应运维要求",
                                "guidance": "量化 SLA",
                                "required_materials": ["服务承诺"],
                            }
                        ]
                    },
                },
            )
            assert custom.status_code == 201, custom.text
            assert custom.json()["is_builtin"] is False
            invalid = await client.post(
                "/api/v1/knowledge-packs",
                headers=headers,
                json={"name": "坏模板", "category": "structure", "content_json": {"sections": []}},
            )
            assert invalid.status_code == 422
            builtin_patch = await client.patch(
                f"/api/v1/knowledge-packs/{packs.json()[0]['id']}",
                json={"name": "越权"},
                headers=headers,
            )
            assert builtin_patch.status_code == 404

            applied = await client.put(
                f"/api/v1/projects/{project_id}/template",
                json={"template_code": "builtin.gov_it", "format_code": "builtin.format_gov"},
                headers=headers,
            )
            assert applied.status_code == 200, applied.text
            sections = applied.json()["sections"]
            assert len(sections) == 7
            assert sections[0]["title"] == "投标函"
            assert sections[-1]["title"] == "项目业绩响应"
            assert applied.json()["project"]["template_code"] == "builtin.gov_it"

            capture = CaptureLLM()
            app.dependency_overrides[get_llm_client] = lambda: capture
            generated = await client.post(
                f"/api/v1/projects/{project_id}/generate",
                headers=headers,
                json={"mode": "llm", "section_ids": [sections[2]["id"]]},
            )
            assert generated.status_code == 200, generated.text
            assert capture.last_user is not None
            prompt = json.loads(capture.last_user)
            assert prompt["template_guide"]["title"] == "技术方案"
            assert "响应技术评分项" in prompt["template_guide"]["purpose"]
            assert "信息系统集成及服务资质" in prompt["available_materials"]["qualifications"]
            app.dependency_overrides.pop(get_llm_client, None)

            material_doc = Document()
            material_doc.add_paragraph("营业执照副本，统一社会信用代码 91330000TEST。")
            material_buffer = BytesIO()
            material_doc.save(material_buffer)
            knowledge_document = await client.post(
                "/api/v1/knowledge/documents",
                files={
                    "file": (
                        "license.docx",
                        material_buffer.getvalue(),
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    )
                },
                data={"title": "营业执照资料", "tags": "资质"},
                headers=headers,
            )
            assert knowledge_document.status_code == 200, knowledge_document.text

            materials = await client.get(
                f"/api/v1/projects/{project_id}/materials", headers=headers
            )
            assert materials.status_code == 200, materials.text
            by_name = {item["name"]: item for item in materials.json()}
            assert by_name["信息系统集成资质"]["status"] == "available"
            assert by_name["投标函"]["status"] == "to_write"
            assert "营业执照资料" in by_name["营业执照"]["knowledge_matches"]

            workspace = await client.get(
                f"/api/v1/projects/{project_id}/workspace", headers=headers
            )
            assert workspace.status_code == 200, workspace.text
            body = workspace.json()
            assert body["template_name"] == "政府采购 · IT 服务"
            assert body["format_name"] == "政采标准格式"
            assert body["sections_total"] == 7
            assert [stage["key"] for stage in body["stages"]] == [
                "parse",
                "compose",
                "produce",
                "deliver",
            ]

            for section in sections:
                check = await client.post(
                    f"/api/v1/sections/{section['id']}/factcheck", headers=headers
                )
                assert check.status_code == 200, check.text
                approved = await client.post(
                    f"/api/v1/sections/{section['id']}/approve",
                    json={"acknowledge_unsourced": True},
                    headers=headers,
                )
                assert approved.status_code == 200, approved.text

            blocked = await client.put(
                f"/api/v1/projects/{project_id}/template",
                json={
                    "template_code": "builtin.enterprise_general",
                    "format_code": "builtin.format_enterprise",
                },
                headers=headers,
            )
            assert blocked.status_code == 422
            assert "approved sections" in blocked.json()["message"]

            exported = await client.post(
                f"/api/v1/projects/{project_id}/export/docx",
                json={"dark_bid": False},
                headers=headers,
            )
            assert exported.status_code == 200, exported.text
            document = Document(BytesIO(exported.content))
            section = document.sections[0]
            assert round(section.left_margin.cm, 2) == 3.18
            assert document.styles["Normal"].font.name == "宋体"
            assert round(document.styles["Normal"].font.size.pt) == 12
            assert "PAGE" in section.footer._element.xml

            other_login = await client.post(
                "/api/v1/auth/login", json={"email": other_email, "password": "Password123!"}
            )
            other_headers = {"Authorization": f"Bearer {other_login.json()['access_token']}"}
            forbidden = await client.put(
                f"/api/v1/projects/{project_id}/template",
                json={"template_code": "builtin.gov_it", "format_code": "builtin.format_gov"},
                headers=other_headers,
            )
            assert forbidden.status_code == 404
    finally:
        await _cleanup([tenant_id, other_tenant_id])
