from io import BytesIO

import pytest
from docx import Document as DocxDocument
from httpx import ASGITransport, AsyncClient

from app.main import app
from tests.support import cleanup_tenants, login, seed_tenant_user


def _final_docx(author: str) -> bytes:
    document = DocxDocument()
    document.core_properties.author = author
    document.add_paragraph("某项目投标文件")
    document.add_paragraph("目录")
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


@pytest.mark.integration
async def test_self_check_flags_cross_project_personnel_and_doc_metadata() -> None:
    tenant_id, _, email = await seed_tenant_user("self-check")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = await login(client, email)
            first = await client.post(
                "/api/v1/projects", json={"name": "自查项目A"}, headers=headers
            )
            second = await client.post(
                "/api/v1/projects", json={"name": "自查项目B"}, headers=headers
            )
            first_id = first.json()["id"]
            second_id = second.json()["id"]

            await client.post(
                f"/api/v1/projects/{first_id}/commitments/snapshot",
                json={"personnel": ["张三"]},
                headers=headers,
            )
            await client.post(
                f"/api/v1/projects/{second_id}/commitments/snapshot",
                json={"personnel": ["张三"]},
                headers=headers,
            )
            final = await client.post(
                f"/api/v1/projects/{first_id}/documents",
                files={
                    "file": (
                        "final.docx",
                        _final_docx("外部制作方"),
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    )
                },
                data={"document_kind": "final"},
                headers=headers,
            )
            assert final.status_code == 200, final.text

            checked = await client.get(
                f"/api/v1/projects/{first_id}/self-check", headers=headers
            )
            assert checked.status_code == 200, checked.text
            codes = {finding["code"] for finding in checked.json()["findings"]}
            assert "PERSONNEL_CROSS_PROJECT" in codes
            assert "DOC_METADATA_MISMATCH" in codes
            assert any("不提供元数据清理" in note for note in checked.json()["notes"])
    finally:
        await cleanup_tenants([tenant_id])
