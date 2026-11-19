"""阶段七集成：文档版本重挂 / 长文档异步解析 / 制度口径问答。"""

from io import BytesIO
from uuid import uuid4

import pytest
from docx import Document
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db.session import SessionLocal
from app.main import app
from app.modules.generation.deps import get_llm_client
from app.modules.knowledge.access import ensure_default_kb
from app.modules.knowledge.models import KnowledgeChunk, KnowledgeDocument
from tests.support import cleanup_tenants, login, seed_tenant_user


def _tender_docx(price_text: str) -> bytes:
    document = Document()
    document.add_heading("招标文件", level=1)
    document.add_paragraph(f"预算金额：{price_text}。")
    document.add_paragraph("投标人须具备信息系统集成资质。")
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


@pytest.mark.integration
async def test_document_versions_and_diff(monkeypatch) -> None:
    tenant_id, _, email = await seed_tenant_user("docver")
    try:
        monkeypatch.setattr(
            "app.modules.projects.service.ASYNC_PARSE_THRESHOLD_BYTES", 1 << 30
        )
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = await login(client, email)
            project = await client.post(
                "/api/v1/projects", json={"name": "版本链项目"}, headers=headers
            )
            project_id = project.json()["id"]

            first = await client.post(
                f"/api/v1/projects/{project_id}/documents",
                files={"file": ("招标文件v1.docx", _tender_docx("人民币 500 万元"), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
                headers=headers,
            )
            assert first.status_code == 200, first.text
            assert first.json()["parse_job"] is None

            second = await client.post(
                f"/api/v1/projects/{project_id}/documents",
                files={"file": ("招标文件v2.docx", _tender_docx("人民币 560 万元"), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
                headers=headers,
            )
            assert second.status_code == 200, second.text

            async with SessionLocal() as session:
                from app.modules.projects.models import TenderDocument

                docs = (
                    await session.execute(
                        select(TenderDocument)
                        .where(TenderDocument.project_id == project_id)
                        .order_by(TenderDocument.version)
                    )
                ).scalars().all()
                assert [doc.version for doc in docs] == [1, 2]
                assert docs[1].supersedes_id == docs[0].id

            diff = await client.get(
                f"/api/v1/projects/{project_id}/document-diff", headers=headers
            )
            assert diff.status_code == 200
            body = diff.json()
            assert body["has_diff"] is True
            assert body["budget"]["changed"] is True
            assert body["budget"]["to"] == "5600000"
    finally:
        await cleanup_tenants([tenant_id])


AMENDMENT_V1 = """
某市政务云运维服务项目招标文件
项目预算：人民币 500 万元。
投标人须具备信息系统集成资质。
系统应支持双活架构。
"""

AMENDMENT_V2 = """
某市政务云运维服务项目补遗文件
项目预算：人民币 560 万元。
投标人须具备信息系统集成资质。
系统应支持三活架构。
"""


@pytest.mark.integration
async def test_amendment_inherits_untouched_dimensions() -> None:
    """补遗只写变更项时，未提到的维度必须沿用上一版，否则下游矩阵/响应表全部归零。"""
    tenant_id, _, email = await seed_tenant_user("amendmerge")
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = await login(client, email)
            project = await client.post(
                "/api/v1/projects", json={"name": "补遗增量项目"}, headers=headers
            )
            project_id = project.json()["id"]

            tender = """
某市智慧政务云运维服务项目招标文件
项目预算：人民币 480 万元。
投标截止时间：2027-02-10 09:30。
投标人须具备信息系统集成及服务资质二级以上。
本项目不接受联合体投标。
评标办法：综合评分法
评分标准：
技术方案（45分）：方案完整性、可行性、针对性。
项目团队（20分）：项目经理具备PMP证书得5分。
售后服务（20分）：响应时间不超过2小时。
投标报价（15分）：满足招标文件要求且最低价得满分。
"""
            first = await client.post(
                f"/api/v1/projects/{project_id}/documents",
                files={"file": ("tender-v1.txt", tender.encode(), "text/plain")},
                headers=headers,
            )
            assert first.status_code == 200, first.text
            baseline = first.json()["constraint"]["payload"]
            scoring_before = len(baseline["scoring_items"])
            qualification_before = len(baseline["qualification_requirements"])
            assert scoring_before >= 3, baseline["scoring_items"]
            assert qualification_before >= 1

            # 只改预算与截止时间的补遗（真实补遗书通常只列变更项）
            second = await client.post(
                f"/api/v1/projects/{project_id}/documents",
                files={
                    "file": (
                        "amendment-v2.txt",
                        "补遗书第 1 号\n项目预算：人民币 520 万元。\n投标截止时间：2027-02-15 09:30。\n".encode(),
                        "text/plain",
                    )
                },
                data={"document_kind": "amendment"},
                headers=headers,
            )
            assert second.status_code == 200, second.text
            merged = second.json()["constraint"]["payload"]
            assert merged["budget"]["amount"] == "5200000", merged["budget"]
            # 关键断言：未被补遗提及的维度必须继承
            assert len(merged["scoring_items"]) == scoring_before, merged["scoring_items"]
            assert (
                len(merged["qualification_requirements"]) == qualification_before
            ), merged["qualification_requirements"]
            inherited = merged["parse_meta"]["amendment_inherited_dimensions"]
            assert "scoring_items" in inherited
            assert "qualification_requirements" in inherited

            # 补遗后下游能力仍可用：矩阵、响应表、合规预检
            matrix = await client.post(
                f"/api/v1/projects/{project_id}/matrix", headers=headers
            )
            assert matrix.status_code == 200, matrix.text
            assert len(matrix.json()) == scoring_before, matrix.json()

            responses = await client.post(
                f"/api/v1/projects/{project_id}/responses/build", headers=headers
            )
            assert responses.status_code == 200, responses.text
            assert responses.json()["total"] > 0, "补遗后响应表不得归零"

            await client.post(f"/api/v1/projects/{project_id}/outline", headers=headers)
            await client.post(
                f"/api/v1/projects/{project_id}/generate",
                json={"mode": "rules"},
                headers=headers,
            )
            compliance = await client.post(
                f"/api/v1/projects/{project_id}/compliance/scan", headers=headers
            )
            assert compliance.status_code == 200, compliance.text
    finally:
        app.dependency_overrides.clear()
        await cleanup_tenants([tenant_id])


@pytest.mark.integration
async def test_amendment_requires_explicit_apply_and_carries_confirmations() -> None:
    tenant_id, _, email = await seed_tenant_user("amendment")
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = await login(client, email)
            project = await client.post(
                "/api/v1/projects", json={"name": "补遗应用项目"}, headers=headers
            )
            project_id = project.json()["id"]

            first = await client.post(
                f"/api/v1/projects/{project_id}/documents",
                files={"file": ("tender-v1.txt", AMENDMENT_V1.encode(), "text/plain")},
                headers=headers,
            )
            assert first.status_code == 200, first.text
            assert first.json()["document"]["document_kind"] == "tender"

            built = await client.post(
                f"/api/v1/projects/{project_id}/responses/build", headers=headers
            )
            target = next(
                item
                for item in built.json()["items"]
                if "信息系统集成资质" in item["requirement_text"]
            )
            patched = await client.patch(
                f"/api/v1/projects/{project_id}/responses/{target['id']}",
                json={"response_status": "responded", "response_note": "资质见附件"},
                headers=headers,
            )
            assert patched.status_code == 200, patched.text

            second = await client.post(
                f"/api/v1/projects/{project_id}/documents",
                files={"file": ("amendment.txt", AMENDMENT_V2.encode(), "text/plain")},
                data={"document_kind": "amendment"},
                headers=headers,
            )
            assert second.status_code == 200, second.text
            assert second.json()["document"]["document_kind"] == "amendment"
            assert second.json()["constraint"]["version"] == 2

            before_apply = await client.get(
                f"/api/v1/projects/{project_id}/responses", headers=headers
            )
            assert before_apply.json()["constraint_version"] == 2
            assert before_apply.json()["total"] == 0, "补遗上传不得自动重建响应"

            diff = await client.get(
                f"/api/v1/projects/{project_id}/document-diff", headers=headers
            )
            assert diff.status_code == 200, diff.text
            body = diff.json()
            assert body["to_document_kind"] == "amendment"
            assert body["has_changes"] is True
            assert body["budget"]["changed"] is True
            assert body["substantive_requirements"]["added"]
            assert body["substantive_requirements"]["removed"]

            refused = await client.post(
                f"/api/v1/projects/{project_id}/document-diff/apply",
                json={"confirm": False},
                headers=headers,
            )
            assert refused.status_code == 422, refused.text

            applied = await client.post(
                f"/api/v1/projects/{project_id}/document-diff/apply",
                json={"confirm": True, "note": "已核对补遗"},
                headers=headers,
            )
            assert applied.status_code == 200, applied.text
            assert applied.json()["constraint_version"] == 2
            assert applied.json()["responses"] >= 2

            after = await client.get(
                f"/api/v1/projects/{project_id}/responses", headers=headers
            )
            assert after.json()["constraint_version"] == 2
            carried = next(
                item
                for item in after.json()["items"]
                if "信息系统集成资质" in item["requirement_text"]
            )
            assert carried["response_status"] == "responded"
            assert carried["response_note"] == "资质见附件"
            changed = next(
                item
                for item in after.json()["items"]
                if "三活架构" in item["requirement_text"]
            )
            assert changed["response_status"] == "pending"

            historical = await client.get(
                f"/api/v1/projects/{project_id}/responses?constraint_version=1",
                headers=headers,
            )
            assert historical.json()["constraint_version"] == 1
            assert any(
                "双活架构" in item["requirement_text"]
                for item in historical.json()["items"]
            )
    finally:
        await cleanup_tenants([tenant_id])


@pytest.mark.integration
async def test_async_parse_job_lifecycle(monkeypatch) -> None:
    tenant_id, _, email = await seed_tenant_user("parsejob")
    try:
        monkeypatch.setattr(
            "app.modules.projects.service.ASYNC_PARSE_THRESHOLD_BYTES", 100
        )
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = await login(client, email)
            project = await client.post(
                "/api/v1/projects", json={"name": "异步解析项目"}, headers=headers
            )
            project_id = project.json()["id"]
            uploaded = await client.post(
                f"/api/v1/projects/{project_id}/documents",
                files={"file": ("大招标文件.docx", _tender_docx("人民币 100 万元"), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
                headers=headers,
            )
            assert uploaded.status_code == 200, uploaded.text
            job = uploaded.json()["parse_job"]
            assert job is not None and job["status"] == "queued"
            assert uploaded.json()["constraint"] is None

            from app.modules.projects.service import run_parse_job

            await run_parse_job(job["id"])

            status = await client.get(f"/api/v1/projects/parse-jobs/{job['id']}", headers=headers)
            assert status.status_code == 200
            assert status.json()["status"] == "succeeded"

            # 幂等：重复执行不再动已成功任务
            await run_parse_job(job["id"])
            again = await client.get(f"/api/v1/projects/parse-jobs/{job['id']}", headers=headers)
            assert again.json()["status"] == "succeeded"
    finally:
        await cleanup_tenants([tenant_id])


@pytest.mark.integration
async def test_stale_parse_job_is_recovered(monkeypatch) -> None:
    from datetime import UTC, datetime, timedelta

    from app.modules.projects.models import ParseJob
    from app.modules.projects.service import recover_stale_parse_jobs

    tenant_id, _, email = await seed_tenant_user("parsejob-recover")
    try:
        monkeypatch.setattr(
            "app.modules.projects.service.ASYNC_PARSE_THRESHOLD_BYTES", 100
        )
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = await login(client, email)
            project = await client.post(
                "/api/v1/projects", json={"name": "重启恢复项目"}, headers=headers
            )
            project_id = project.json()["id"]
            uploaded = await client.post(
                f"/api/v1/projects/{project_id}/documents",
                files={"file": ("恢复.docx", _tender_docx("人民币 120 万元"), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
                headers=headers,
            )
            job_id = uploaded.json()["parse_job"]["id"]

            async with SessionLocal() as session:
                original = await session.get(ParseJob, job_id)
                assert original is not None
                stale_job = ParseJob(
                    tenant_id=tenant_id,
                    project_id=project_id,
                    document_id=original.document_id,
                    status="queued",
                    progress=0,
                    created_by=original.created_by,
                    created_at=datetime.now(UTC) - timedelta(hours=1),
                )
                session.add(stale_job)
                await session.commit()
                await session.refresh(stale_job)
                stale_job_id = str(stale_job.id)

            recovered = await recover_stale_parse_jobs()
            assert recovered >= 1
            status = await client.get(
                f"/api/v1/projects/parse-jobs/{stale_job_id}", headers=headers
            )
            assert status.json()["status"] == "succeeded"
    finally:
        await cleanup_tenants([tenant_id])


class _FakeQaLLM:
    def configured(self) -> bool:
        return True

    async def complete(self, *, system: str, user: str) -> str:
        assert "[1]" in user
        return "按制度 [1]，报销应在 30 日内提交。"


@pytest.mark.integration
async def test_policy_qa_requires_sources_and_answers() -> None:
    tenant_id, _, email = await seed_tenant_user("policyqa")
    try:
        async with SessionLocal() as session:
            kb = await ensure_default_kb(session, tenant_id)
            doc = KnowledgeDocument(
                tenant_id=tenant_id,
                kb_id=kb.id,
                classification=kb.classification,
                title="财务报销制度",
                kind="policy",
                filename="财务报销制度.docx",
                size_bytes=1024,
                file_hash=uuid4().hex,
                object_ref="test://policy",
                status="parsed",
            )
            session.add(doc)
            await session.flush()
            session.add(
                KnowledgeChunk(
                    tenant_id=tenant_id,
                    kb_id=kb.id,
                    classification=kb.classification,
                    document_id=doc.id,
                    chunk_index=0,
                    text="员工差旅费用报销应当在出差结束后 30 日内提交财务部审核，逾期不予受理。",
                )
            )
            await session.commit()

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = await login(client, email)
            project = await client.post(
                "/api/v1/projects", json={"name": "口径问答项目"}, headers=headers
            )
            project_id = project.json()["id"]

            app.dependency_overrides[get_llm_client] = lambda: _FakeQaLLM()
            qa = await client.post(
                f"/api/v1/projects/{project_id}/policy-qa",
                json={"question": "差旅报销多久内提交？"},
                headers=headers,
            )
            assert qa.status_code == 200, qa.text
            body = qa.json()
            assert "30 日" in body["answer"]
            assert body["sources"] and body["sources"][0]["title"] == "财务报销制度"

            empty = await client.post(
                f"/api/v1/projects/{project_id}/policy-qa",
                json={"question": "火星基地建设标准是什么？"},
                headers=headers,
            )
            assert empty.status_code == 200
            assert empty.json()["sources"] == []
            assert "未找到" in empty.json()["answer"]
    finally:
        app.dependency_overrides.clear()
        await cleanup_tenants([tenant_id])
