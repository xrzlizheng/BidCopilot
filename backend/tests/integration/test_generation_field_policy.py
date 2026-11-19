from io import BytesIO

import pytest
from docx import Document as DocxDocument
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db.session import SessionLocal
from app.main import app
from app.modules.identity.models import User
from tests.support import cleanup_tenants, login, seed_tenant_user

TENDER = """
某市政务云运维服务项目招标文件
投标截止时间：2027-01-08 10:00。
投标人须具备信息系统集成资质。
评分采用综合评分法：
1. 技术方案：40 分
2. 投标报价：60 分
"""


def _docx_text(content: bytes) -> str:
    document = DocxDocument(BytesIO(content))
    parts = [paragraph.text for paragraph in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                parts.append(cell.text)
    return "\n".join(parts)


@pytest.mark.integration
async def test_hidden_asset_fields_do_not_enter_generated_docx() -> None:
    tenant_id, admin_id, email = await seed_tenant_user("generation-mask")
    hidden_name = "绝密资质XYZ-2026"
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = await login(client, email)
            company = await client.post(
                "/api/v1/companies", json={"name": "密级测试公司"}, headers=headers
            )
            assert company.status_code == 201, company.text
            company_id = company.json()["id"]

            policy = await client.put(
                "/api/v1/asset-field-policies",
                json={
                    "resource_type": "qualifications",
                    "field_name": "name",
                    "min_clearance": "confidential",
                    "mask_strategy": "hide",
                },
                headers=headers,
            )
            assert policy.status_code == 200, policy.text
            qualification = await client.post(
                "/api/v1/assets/qualifications",
                json={"company_id": company_id, "name": hidden_name},
                headers=headers,
            )
            assert qualification.status_code == 201, qualification.text

            project = await client.post(
                "/api/v1/projects",
                json={"name": "密级生成项目", "company_id": company_id},
                headers=headers,
            )
            assert project.status_code == 201, project.text
            project_id = project.json()["id"]
            uploaded = await client.post(
                f"/api/v1/projects/{project_id}/documents",
                files={"file": ("tender.txt", TENDER.encode(), "text/plain")},
                headers=headers,
            )
            assert uploaded.status_code == 200, uploaded.text

            outline = await client.post(
                f"/api/v1/projects/{project_id}/outline", headers=headers
            )
            assert outline.status_code == 200, outline.text
            generated = await client.post(
                f"/api/v1/projects/{project_id}/generate",
                json={"mode": "rules"},
                headers=headers,
            )
            assert generated.status_code == 200, generated.text

            built = await client.post(
                f"/api/v1/projects/{project_id}/responses/build", headers=headers
            )
            assert built.status_code == 200, built.text
            ids = [item["id"] for item in built.json()["items"]]
            if ids:
                batched = await client.post(
                    f"/api/v1/projects/{project_id}/responses/batch",
                    json={
                        "response_ids": ids,
                        "response_status": "responded",
                        "response_note": "统一响应",
                    },
                    headers=headers,
                )
                assert batched.status_code == 200, batched.text

            sections = await client.get(
                f"/api/v1/projects/{project_id}/sections", headers=headers
            )
            assert sections.status_code == 200, sections.text
            for section in sections.json():
                approved = await client.post(
                    f"/api/v1/sections/{section['id']}/approve",
                    json={"acknowledge_unsourced": True},
                    headers=headers,
                )
                assert approved.status_code == 200, approved.text

            exported = await client.post(
                f"/api/v1/projects/{project_id}/export/docx",
                json={"dark_bid": False, "acknowledge_unresolved": True},
                headers=headers,
            )
            assert exported.status_code == 200, exported.text
            assert hidden_name not in _docx_text(exported.content)

        async with SessionLocal() as session:
            admin = (
                await session.execute(select(User).where(User.id == admin_id))
            ).scalar_one()
            admin.clearance = "confidential"
            await session.commit()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = await login(client, email)
            visible = await client.get("/api/v1/assets/qualifications", headers=headers)
            assert visible.status_code == 200, visible.text
            row = next(item for item in visible.json()["items"] if item["id"] == qualification.json()["id"])
            assert row["name"] == hidden_name, "高密级用户应能恢复查看字段"
    finally:
        await cleanup_tenants([tenant_id])
