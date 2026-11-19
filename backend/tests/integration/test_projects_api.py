import base64
from io import BytesIO
from uuid import UUID, uuid4

import pytest
from docx import Document as DocxDocument
from httpx import ASGITransport, AsyncClient
from openpyxl import load_workbook
from pypdf import PdfReader
from sqlalchemy import delete, select

from app.core.security import hash_password
from app.db.session import SessionLocal
from app.main import app
from app.modules.audit.models import AuditEvent
from app.modules.generation.deps import get_llm_client
from app.modules.identity.models import Role, Tenant, User, UserRole
from app.modules.identity.roles import SYSTEM_ROLES
from app.modules.tender.deps import get_ocr_provider
from app.modules.tender.ocr import OcrPage, OcrQuotaError, OcrResult
from tests.support import sample_tender_docx


class FakeLLM:
    def configured(self) -> bool:
        return True

    async def complete(self, *, system: str, user: str) -> str:
        return '{"blocks":[{"type":"paragraph","text":"LLM 生成的响应内容。"}]}'


class FakeMinimaxProvider:
    """OcrProvider 替身：provider 名沿用 minimax，锁定 parse_meta 断言语义。"""

    name = "minimax"

    async def recognize(
        self, content: bytes, filename: str, *, max_pages: int
    ) -> OcrResult:
        return OcrResult(
            pages=[
                OcrPage(
                    page_no=1,
                    text=(
                        "技术方案：20分\n"
                        "项目业绩：10分\n"
                        "资格要求：投标人须具备信息系统集成资质\n"
                        "投标截止时间：2026年11月10日 09:00"
                    ),
                )
            ],
            tables=[],
            provider=self.name,
            structure_used=False,
        )


class QuotaMinimaxProvider:
    name = "minimax"

    async def recognize(
        self, content: bytes, filename: str, *, max_pages: int
    ) -> OcrResult:
        raise OcrQuotaError("insufficient balance")


PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


async def _seed_tenant(label: str) -> tuple[UUID, str]:
    async with SessionLocal() as session:
        roles: dict[str, Role] = {}
        for code, spec in SYSTEM_ROLES.items():
            role = (
                await session.execute(select(Role).where(Role.tenant_id.is_(None), Role.code == code))
            ).scalar_one_or_none()
            if role is None:
                role = Role(
                    tenant_id=None,
                    code=code,
                    name=spec["name"],
                    permissions=list(spec["permissions"]),
                    is_system=True,
                )
                session.add(role)
                await session.flush()
            else:
                role.name = spec["name"]
                role.permissions = list(spec["permissions"])
            roles[code] = role
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
        session.add(UserRole(tenant_id=tenant.id, user_id=user.id, role_id=roles["tenant_admin"].id, scope_type="tenant", scope_id=tenant.id))
        await session.commit()
        return tenant.id, email


async def _cleanup(tenant_ids: list[UUID]) -> None:
    async with SessionLocal() as session:
        await session.execute(delete(AuditEvent).where(AuditEvent.tenant_id.in_(tenant_ids)))
        await session.execute(delete(Tenant).where(Tenant.id.in_(tenant_ids)))
        await session.commit()


@pytest.mark.integration
async def test_project_tender_intelligence_pipeline() -> None:
    tenant_id, email = await _seed_tenant("projects-a")
    other_tenant_id, other_email = await _seed_tenant("projects-b")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            login = await client.post(
                "/api/v1/auth/login", json={"email": email, "password": "Password123!"}
            )
            assert login.status_code == 200, login.text
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

            company = await client.post(
                "/api/v1/companies",
                json={"name": "测试投标企业", "credit_code": "91510000PROJECT01"},
                headers=headers,
            )
            assert company.status_code == 201, company.text
            company_id = company.json()["id"]

            qualification = await client.post(
                "/api/v1/assets/qualifications",
                json={
                    "company_id": company_id,
                    "name": "涉密信息系统集成",
                    "level": "乙级",
                    "cert_no": "SEC-001",
                    "valid_to": "2027-01-01",
                },
                headers=headers,
            )
            assert qualification.status_code == 201, qualification.text

            personnel = await client.post(
                "/api/v1/assets/personnel",
                json={
                    "company_id": company_id,
                    "name": "张三",
                    "cert_type": "PMP",
                    "cert_no": "PMP-001",
                    "valid_to": "2027-01-01",
                },
                headers=headers,
            )
            assert personnel.status_code == 201, personnel.text

            for index in range(2):
                performance = await client.post(
                    "/api/v1/assets/performances",
                    json={
                        "company_id": company_id,
                        "project_name": f"政务云系统集成项目 {index + 1}",
                        "amount": "1000000",
                        "complete_date": "2025-06-30",
                    },
                    headers=headers,
                )
                assert performance.status_code == 201, performance.text

            project = await client.post(
                "/api/v1/projects",
                json={
                    "company_id": company_id,
                    "name": "某涉密系统集成项目",
                    "bid_deadline": "2026-11-10T09:00:00Z",
                },
                headers=headers,
            )
            assert project.status_code == 201, project.text
            project_id = project.json()["id"]

            upload = await client.post(
                f"/api/v1/projects/{project_id}/documents",
                files={
                    "file": (
                        "tender.docx",
                        sample_tender_docx(),
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    )
                },
                headers=headers,
            )
            assert upload.status_code == 200, upload.text
            uploaded = upload.json()
            assert uploaded["document"]["parse_status"] == "parsed"
            assert uploaded["constraint"]["version"] == 1
            assert len(uploaded["constraint"]["payload"]["scoring_items"]) >= 3
            assert uploaded["constraint"]["payload"]["reject_clauses"]

            constraints = await client.get(
                f"/api/v1/projects/{project_id}/constraints", headers=headers
            )
            assert constraints.status_code == 200
            assert constraints.json()["payload"]["budget"]["amount"] == "1200000"

            items = await client.get(
                f"/api/v1/projects/{project_id}/scoring-items", headers=headers
            )
            assert items.status_code == 200
            assert len(items.json()) >= 3

            matrix = await client.post(
                f"/api/v1/projects/{project_id}/matrix", headers=headers
            )
            assert matrix.status_code == 200, matrix.text
            matrix_rows = matrix.json()
            assert len(matrix_rows) >= 3
            assert all(row["coverage_status"] == "covered" for row in matrix_rows)

            qualification_check = await client.post(
                f"/api/v1/projects/{project_id}/qualification-check", headers=headers
            )
            assert qualification_check.status_code == 200, qualification_check.text
            summary = qualification_check.json()["summary"]
            assert summary["FAIL"] == 0
            assert summary["UNKNOWN"] == 0
            assert summary["PASS"] >= 3

            decision = await client.post(
                f"/api/v1/projects/{project_id}/decision", headers=headers
            )
            assert decision.status_code == 200, decision.text
            assert decision.json()["decision"]["status"] == "Go"

            saved = await client.get(
                f"/api/v1/projects/{project_id}/decision", headers=headers
            )
            assert saved.status_code == 200
            assert saved.json()["status"] == "Go"

            outline = await client.post(
                f"/api/v1/projects/{project_id}/outline", headers=headers
            )
            assert outline.status_code == 200, outline.text
            sections = outline.json()
            assert len(sections) >= 5

            llm_without_config = await client.post(
                f"/api/v1/projects/{project_id}/generate",
                json={"mode": "llm"},
                headers=headers,
            )
            assert llm_without_config.status_code == 503

            generated = await client.post(
                f"/api/v1/projects/{project_id}/generate",
                json={"mode": "rules"},
                headers=headers,
            )
            assert generated.status_code == 200, generated.text
            assert generated.json()["status"] == "succeeded"
            assert generated.json()["sections_generated"] == len(sections)

            section_list = await client.get(
                f"/api/v1/projects/{project_id}/sections", headers=headers
            )
            assert section_list.status_code == 200
            section_rows = section_list.json()
            assert any(row["content_text"] for row in section_rows)

            commitment = next(
                row for row in section_rows if row["section_type"] == "commitment"
            )
            app.dependency_overrides[get_llm_client] = lambda: FakeLLM()
            try:
                llm_generated = await client.post(
                    f"/api/v1/projects/{project_id}/generate",
                    json={"mode": "llm", "section_ids": [commitment["id"]]},
                    headers=headers,
                )
                assert llm_generated.status_code == 200, llm_generated.text
                assert llm_generated.json()["sections_generated"] == 1
                sections_after_llm = await client.get(
                    f"/api/v1/projects/{project_id}/sections", headers=headers
                )
                llm_section = next(
                    row
                    for row in sections_after_llm.json()
                    if row["id"] == commitment["id"]
                )
                assert llm_section["generation_mode"] == "llm"
                assert "LLM 生成的响应内容" in llm_section["content_text"]
            finally:
                app.dependency_overrides.pop(get_llm_client, None)

            scoring = next(row for row in section_rows if row["section_type"] == "scoring")
            knowledge_document = await client.post(
                "/api/v1/knowledge/documents",
                files={
                    "file": (
                        "technical.docx",
                        sample_tender_docx(),
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    )
                },
                data={"title": "技术方案知识", "tags": "技术方案"},
                headers=headers,
            )
            assert knowledge_document.status_code == 200, knowledge_document.text
            knowledge_context = await client.get(
                f"/api/v1/projects/{project_id}/sections/{scoring['id']}/knowledge-context",
                headers=headers,
            )
            assert knowledge_context.status_code == 200, knowledge_context.text
            assert knowledge_context.json()["chunks"]
            assert scoring["scoring_item_id"], "scoring section must link to a scoring item"
            scoring_item = next(
                row
                for row in matrix_rows
                if row["scoring_item_id"] == scoring["scoring_item_id"]
            )
            query = knowledge_context.json()["query"]
            assert scoring["title"] in query
            assert scoring_item["item"] in query

            image_png = bytes.fromhex(
                "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
                "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
            )
            knowledge_image = await client.post(
                "/api/v1/knowledge/images",
                files={"file": ("technical.png", image_png, "image/png")},
                data={"title": "技术方案架构图", "tags": "技术方案"},
                headers=headers,
            )
            assert knowledge_image.status_code == 200, knowledge_image.text
            image_id = knowledge_image.json()["id"]
            visible_context = await client.get(
                f"/api/v1/projects/{project_id}/sections/{scoring['id']}/knowledge-context",
                headers=headers,
            )
            assert any(image["id"] == image_id for image in visible_context.json()["images"])
            await client.patch(
                f"/api/v1/knowledge/images/{image_id}",
                json={"sensitive": True},
                headers=headers,
            )
            filtered_context = await client.get(
                f"/api/v1/projects/{project_id}/sections/{scoring['id']}/knowledge-context",
                headers=headers,
            )
            assert all(image["id"] != image_id for image in filtered_context.json()["images"])

            structured = await client.patch(
                f"/api/v1/sections/{scoring['id']}",
                json={
                    "content_json": [
                        {"type": "paragraph", "text": "结构化段落"},
                        {
                            "type": "table",
                            "headers": ["评分项", "分值"],
                            "rows": [["技术方案", "40"]],
                        },
                    ]
                },
                headers=headers,
            )
            assert structured.status_code == 200, structured.text
            refreshed = await client.get(
                f"/api/v1/projects/{project_id}/sections", headers=headers
            )
            preserved = next(row for row in refreshed.json() if row["id"] == scoring["id"])
            assert preserved["content_json"][1]["type"] == "table"
            assert preserved["content_json"][1]["rows"] == [["技术方案", "40"]]
            assert "技术方案" in preserved["content_text"]

            invalid_blocks = await client.patch(
                f"/api/v1/sections/{scoring['id']}",
                json={
                    "content_json": [
                        {
                            "type": "image",
                            "image_id": "00000000-0000-0000-0000-000000000000",
                            "caption": "编造图片",
                        }
                    ]
                },
                headers=headers,
            )
            assert invalid_blocks.status_code == 422

            edited = await client.patch(
                f"/api/v1/sections/{scoring['id']}",
                json={"content_text": "本项目报价 999999 元，工期 12 个月。"},
                headers=headers,
            )
            assert edited.status_code == 200, edited.text

            checked = await client.post(
                f"/api/v1/sections/{scoring['id']}/factcheck", headers=headers
            )
            assert checked.status_code == 200, checked.text
            assert checked.json()["section"]["unsourced_count"] >= 1
            assert any(
                link["status"] == "unsourced" for link in checked.json()["evidence"]
            )
            rejected_approval = await client.post(
                f"/api/v1/sections/{scoring['id']}/approve",
                json={"acknowledge_unsourced": False},
                headers=headers,
            )
            assert rejected_approval.status_code == 422
            approved = await client.post(
                f"/api/v1/sections/{scoring['id']}/approve",
                json={"acknowledge_unsourced": True},
                headers=headers,
            )
            assert approved.status_code == 200, approved.text
            assert approved.json()["status"] == "approved"

            cover = next(row for row in section_rows if row["section_type"] == "cover")
            qualification_section = next(
                row for row in section_rows if row["section_type"] == "qualification"
            )
            for section in (cover, qualification_section):
                response = await client.post(
                    f"/api/v1/sections/{section['id']}/approve",
                    json={"acknowledge_unsourced": True},
                    headers=headers,
                )
                assert response.status_code == 200, response.text

            # 导出闸门要求逐条响应闭环；这里把响应表补齐，走"合规放行"而不是"强制绕过"
            built = await client.post(
                f"/api/v1/projects/{project_id}/responses/build", headers=headers
            )
            assert built.status_code == 200, built.text
            response_ids = [item["id"] for item in built.json()["items"]]
            assert response_ids, built.text
            batched = await client.post(
                f"/api/v1/projects/{project_id}/responses/batch",
                headers=headers,
                json={
                    "response_ids": response_ids,
                    "response_status": "responded",
                    "response_note": "逐条响应，见对应章节",
                },
            )
            assert batched.status_code == 200, batched.text
            assert batched.json()["unresolved"] == 0, batched.text

            exported = await client.post(
                f"/api/v1/projects/{project_id}/export/docx",
                json={"dark_bid": False},
                headers=headers,
            )
            assert exported.status_code == 200, exported.text
            assert exported.content[:2] == b"PK"
            document = DocxDocument(BytesIO(exported.content))
            assert any("投标函" in paragraph.text for paragraph in document.paragraphs)

            dark = await client.post(
                f"/api/v1/projects/{project_id}/export/docx",
                json={"dark_bid": True},
                headers=headers,
            )
            assert dark.status_code == 200, dark.text
            dark_document = DocxDocument(BytesIO(dark.content))
            dark_text = "\n".join(paragraph.text for paragraph in dark_document.paragraphs)
            for table in dark_document.tables:
                for row in table.rows:
                    for cell in row.cells:
                        dark_text += "\n" + cell.text
            assert "测试投标企业" not in dark_text
            assert "张三" not in dark_text

            # 6.2 一键匿名化：绕过响应表闸门直接产出脱敏 docx，并清空核心属性
            anonymized = await client.post(
                f"/api/v1/projects/{project_id}/anonymize", headers=headers
            )
            assert anonymized.status_code == 200, anonymized.text
            assert anonymized.content[:2] == b"PK"
            anon_document = DocxDocument(BytesIO(anonymized.content))
            anon_text = "\n".join(paragraph.text for paragraph in anon_document.paragraphs)
            for table in anon_document.tables:
                for row in table.rows:
                    for cell in row.cells:
                        anon_text += "\n" + cell.text
            assert "测试投标企业" not in anon_text
            assert "张三" not in anon_text
            props = anon_document.core_properties
            assert not str(getattr(props, "author", "") or "").strip()
            assert not str(getattr(props, "last_modified_by", "") or "").strip()

            scan = await client.post(
                f"/api/v1/projects/{project_id}/compliance/scan", headers=headers
            )
            assert scan.status_code == 200, scan.text
            report = scan.json()
            assert report["status"] == "completed"
            assert report["rule_version"]
            rule_ids = {finding["rule_id"] for finding in report["findings"]}
            assert {"DARK-NAME-001", "EVID-UNSOURCED-001", "FORMAT-FONT-001"} <= rule_ids
            assert report["summary_json"]["hard"] > 0
            assert report["summary_json"]["soft"] > 0

            finding_id = report["findings"][0]["id"]
            updated = await client.patch(
                f"/api/v1/findings/{finding_id}",
                json={"status": "accepted"},
                headers=headers,
            )
            assert updated.status_code == 200, updated.text
            assert updated.json()["status"] == "accepted"

            reports = await client.get(
                f"/api/v1/projects/{project_id}/compliance/reports", headers=headers
            )
            assert reports.status_code == 200
            assert len(reports.json()) >= 1
            latest = await client.get(
                f"/api/v1/projects/{project_id}/compliance/latest", headers=headers
            )
            assert latest.status_code == 200
            assert latest.json()["id"] == report["id"]

            xlsx = await client.post(
                f"/api/v1/compliance/reports/{report['id']}/export",
                json={"format": "xlsx"},
                headers=headers,
            )
            assert xlsx.status_code == 200, xlsx.text
            workbook = load_workbook(BytesIO(xlsx.content))
            assert {"Summary", "Findings"} <= set(workbook.sheetnames)

            pdf = await client.post(
                f"/api/v1/compliance/reports/{report['id']}/export",
                json={"format": "pdf"},
                headers=headers,
            )
            assert pdf.status_code == 200, pdf.text
            assert pdf.content[:4] == b"%PDF"
            assert len(PdfReader(BytesIO(pdf.content)).pages) >= 1

            other_login = await client.post(
                "/api/v1/auth/login",
                json={"email": other_email, "password": "Password123!"},
            )
            other_headers = {"Authorization": f"Bearer {other_login.json()['access_token']}"}
            forbidden = await client.get(
                f"/api/v1/projects/{project_id}", headers=other_headers
            )
            assert forbidden.status_code == 404
            forbidden_report = await client.get(
                f"/api/v1/compliance/reports/{report['id']}", headers=other_headers
            )
            assert forbidden_report.status_code == 404
            forbidden_export = await client.post(
                f"/api/v1/compliance/reports/{report['id']}/export",
                json={"format": "pdf"},
                headers=other_headers,
            )
            assert forbidden_export.status_code == 404
    finally:
        await _cleanup([tenant_id, other_tenant_id])


@pytest.mark.integration
async def test_ocr_image_upload_and_explicit_failures() -> None:
    tenant_id, email = await _seed_tenant("ocr-a")
    app.dependency_overrides[get_ocr_provider] = lambda: FakeMinimaxProvider()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            login = await client.post(
                "/api/v1/auth/login", json={"email": email, "password": "Password123!"}
            )
            assert login.status_code == 200, login.text
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

            project = await client.post(
                "/api/v1/projects",
                json={"name": "扫描件 OCR 项目"},
                headers=headers,
            )
            assert project.status_code == 201, project.text
            project_id = project.json()["id"]

            uploaded = await client.post(
                f"/api/v1/projects/{project_id}/documents",
                files={"file": ("scan.png", PNG_BYTES, "image/png")},
                headers=headers,
            )
            assert uploaded.status_code == 200, uploaded.text
            body = uploaded.json()
            assert body["document"]["parse_status"] == "parsed"
            assert body["document"]["ocr_used"] is True
            assert body["constraint"]["payload"]["parse_meta"]["ocr_used"] is True
            assert body["constraint"]["payload"]["parse_meta"]["ocr_provider"] == "minimax"
            assert len(body["constraint"]["payload"]["scoring_items"]) >= 2
            assert body["constraint"]["payload"]["qualification_requirements"]
            assert body["constraint"]["payload"]["timeline"]

            app.dependency_overrides[get_ocr_provider] = lambda: QuotaMinimaxProvider()
            quota = await client.post(
                f"/api/v1/projects/{project_id}/documents",
                files={"file": ("quota.png", PNG_BYTES, "image/png")},
                headers=headers,
            )
            assert quota.status_code == 200, quota.text
            assert quota.json()["document"]["parse_status"] == "failed"
            assert quota.json()["document"]["ocr_used"] is False
            assert "ocr_failed" in quota.json()["document"]["parse_error"]
            assert "insufficient balance" in quota.json()["document"]["parse_error"]
            assert quota.json()["constraint"] is None

            app.dependency_overrides[get_ocr_provider] = lambda: None
            unconfigured = await client.post(
                f"/api/v1/projects/{project_id}/documents",
                files={"file": ("unconfigured.png", PNG_BYTES, "image/png")},
                headers=headers,
            )
            assert unconfigured.status_code == 200, unconfigured.text
            assert unconfigured.json()["document"]["parse_status"] == "failed"
            assert "OCR 服务未配置" in unconfigured.json()["document"]["parse_error"]
    finally:
        app.dependency_overrides.pop(get_ocr_provider, None)
        await _cleanup([tenant_id])



async def test_bid_deadline_backfilled_only_from_explicit_clock_time() -> None:
    _, email = await _seed_tenant("deadline-a")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        login = await client.post(
            "/api/v1/auth/login", json={"email": email, "password": "Password123!"}
        )
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        async def deadline_after_upload(label: str, tender: str) -> object:
            project = await client.post("/api/v1/projects", json={"name": label}, headers=headers)
            project_id = project.json()["id"]
            assert project.json()["bid_deadline"] is None
            uploaded = await client.post(
                f"/api/v1/projects/{project_id}/documents",
                files={"file": ("tender.txt", tender.encode("utf-8"), "text/plain")},
                headers=headers,
            )
            assert uploaded.status_code == 200, uploaded.text
            assert uploaded.json()["document"]["parse_status"] == "parsed"
            fetched = await client.get(f"/api/v1/projects/{project_id}", headers=headers)
            return fetched.json()["project"]["bid_deadline"]

        with_clock = await deadline_after_upload(
            "有钟点截止项目", "某运维服务项目招标文件\n投标截止时间为 2027年3月5日 14:30。\n"
        )
        assert with_clock is not None and str(with_clock).startswith("2027-03-05T06:30"), with_clock

        date_only = await deadline_after_upload(
            "无钟点截止项目", "某运维服务项目招标文件\n投标截止时间为 2027年3月5日。\n"
        )
        assert date_only is None

        silent = await deadline_after_upload("无截止表述项目", "某运维服务项目招标文件\n预算 100 万元。\n")
        assert silent is None
