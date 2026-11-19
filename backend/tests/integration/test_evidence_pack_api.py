import json
import zipfile
from io import BytesIO

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from tests.support import cleanup_tenants, login, seed_tenant_user

TENDER = """
某市政务云运维服务项目招标文件
投标截止时间：2027-01-08 10:00。
投标人须具备信息系统集成资质。
评分采用综合评分法：
1. 技术方案：40 分
2. 投标报价：60 分
"""


@pytest.mark.integration
async def test_evidence_pack_contains_decision_readiness_and_ai_usage() -> None:
    tenant_id, _, email = await seed_tenant_user("evidence-pack")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = await login(client, email)
            project = await client.post(
                "/api/v1/projects",
                json={"name": "证据包项目", "code": 'TEST"code; 2026/01'},
                headers=headers,
            )
            project_id = project.json()["id"]
            uploaded = await client.post(
                f"/api/v1/projects/{project_id}/documents",
                files={"file": ("tender.txt", TENDER.encode(), "text/plain")},
                headers=headers,
            )
            assert uploaded.status_code == 200, uploaded.text
            readiness = await client.get(
                f"/api/v1/projects/{project_id}/readiness", headers=headers
            )
            assert readiness.status_code == 200, readiness.text
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
            card = await client.post(
                f"/api/v1/projects/{project_id}/decision-card",
                json={"inputs": {"quote_amount": 1000000, "estimated_cost": 900000, "tax_rate": 0.09}},
                headers=headers,
            )
            assert card.status_code == 200, card.text

            exported = await client.get(
                f"/api/v1/projects/{project_id}/evidence-pack", headers=headers
            )
            assert exported.status_code == 200, exported.text
            assert exported.headers["content-type"] == "application/zip"
            # [P1-3] 验证文件名白名单过滤，无双引号/分号/斜杠注入
            disposition = exported.headers.get("content-disposition", "")
            assert 'filename="evidence-pack-TEST_code__2026_01.zip"' in disposition

        with zipfile.ZipFile(BytesIO(exported.content)) as archive:
            names = set(archive.namelist())
            assert {
                "manifest.json",
                "decision-card.json",
                "readiness.json",
                "responses.xlsx",
                "compliance-report.json",
                "ai-usage.json",
                "audit-events.json",
            } <= names
            manifest = json.loads(archive.read("manifest.json"))
            decision = json.loads(archive.read("decision-card.json"))
            readiness_payload = json.loads(archive.read("readiness.json"))
            ai_usage = json.loads(archive.read("ai-usage.json"))
            assert manifest["project"]["id"] == project_id
            assert decision["inputs"]["quote_amount"] in {"1000000", "1000000.00"}
            assert readiness_payload["summary"]["total"] >= 30
            assert ai_usage, "规则生成也应有 AI 使用记录"
    finally:
        await cleanup_tenants([tenant_id])
