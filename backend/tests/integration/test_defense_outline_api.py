import pytest
from httpx import ASGITransport, AsyncClient

from app.db.session import SessionLocal
from app.main import app
from app.modules.projects.models import BidDecisionRun
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
async def test_defense_outline_and_docx_export() -> None:
    tenant_id, _, email = await seed_tenant_user("defense")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = await login(client, email)
            project = await client.post(
                "/api/v1/projects", json={"name": "答辩项目"}, headers=headers
            )
            project_id = project.json()["id"]
            uploaded = await client.post(
                f"/api/v1/projects/{project_id}/documents",
                files={"file": ("tender.txt", TENDER.encode(), "text/plain")},
                headers=headers,
            )
            assert uploaded.status_code == 200, uploaded.text

            outline = await client.get(
                f"/api/v1/projects/{project_id}/defense-outline?minutes=20", headers=headers
            )
            assert outline.status_code == 200, outline.text
            body = outline.json()
            assert body["slides"]
            assert body["allocated_minutes"] >= 10
            assert any("技术方案" in slide["title"] for slide in body["slides"])

            # [P1-2] 决策记录存在但无报价口径时，答辩提纲不崩溃。
            # 注：inputs_json 在模型与迁移 0046 上均为 NOT NULL，显式写入 None 会被
            # 数据库拒绝（IntegrityError），因此真实可达的边界是「空对象」而非 None。
            async with SessionLocal() as session:
                session.add(
                    BidDecisionRun(
                        tenant_id=tenant_id,
                        project_id=project_id,
                        run_no=1,
                        engine_mode="full",
                        engine_version="test",
                        inputs_json={},
                        computed_status="Go",
                    )
                )
                await session.commit()

            outline_with_run = await client.get(
                f"/api/v1/projects/{project_id}/defense-outline?minutes=20", headers=headers
            )
            assert outline_with_run.status_code == 200, outline_with_run.text

            exported = await client.get(
                f"/api/v1/projects/{project_id}/defense-outline/export?minutes=20",
                headers=headers,
            )
            assert exported.status_code == 200, exported.text
            assert exported.content.startswith(b"PK")
            assert "wordprocessingml" in exported.headers["content-type"]
    finally:
        await cleanup_tenants([tenant_id])
