from io import BytesIO

import pytest
from docx import Document as DocxDocument
from httpx import ASGITransport, AsyncClient

from app.main import app
from tests.support import cleanup_tenants, login, seed_tenant_user


def _final_docx() -> bytes:
    document = DocxDocument()
    document.add_paragraph("某项目投标文件")
    document.add_paragraph("目录")
    document.add_paragraph("第一章 技术方案")
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


@pytest.mark.integration
async def test_final_bid_document_is_analyzed_without_new_constraint() -> None:
    tenant_id, _, email = await seed_tenant_user("final-doc")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = await login(client, email)
            project = await client.post(
                "/api/v1/projects", json={"name": "最终标书项目"}, headers=headers
            )
            project_id = project.json()["id"]
            uploaded = await client.post(
                f"/api/v1/projects/{project_id}/documents",
                files={
                    "file": (
                        "final.docx",
                        _final_docx(),
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    )
                },
                data={"document_kind": "final"},
                headers=headers,
            )
            assert uploaded.status_code == 200, uploaded.text
            body = uploaded.json()
            assert body["document"]["document_kind"] == "final"
            assert body["document"]["parse_status"] == "analyzed"
            assert body["document"]["assembly_meta"]["analyzed"] is True
            assert body["document"]["assembly_meta"]["has_toc"] is True
            assert body["constraint"] is None, "最终标书不得生成新的招标约束版本"
    finally:
        await cleanup_tenants([tenant_id])
