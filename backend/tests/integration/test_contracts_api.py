"""合同审查 API 集成测试：上传→切分→规则+三方对照→确认流→导出→租户隔离。"""

from io import BytesIO
from uuid import UUID, uuid4

import pytest
from docx import Document
from httpx import ASGITransport, AsyncClient
from openpyxl import load_workbook
from sqlalchemy import delete, select

from app.db.session import SessionLocal
from app.main import app
from app.modules.contracts.models import ContractFinding
from app.modules.generation.deps import get_llm_client
from app.modules.projects.models import TenderConstraint, TenderDocument
from tests.support import cleanup_tenants, login, seed_tenant_user


def _contract_docx() -> bytes:
    # 正文需超过缺失类规则的最小长度阈值，否则走 unknown 语义而不是缺失告警
    filler = (
        "双方经友好协商，就本项目达成如下条款，明确双方权利义务与履行安排；"
        "本合同经双方签署后生效，对双方均具有法律约束力，双方应共同遵守执行，"
        "未尽事宜由双方另行协商并签订书面补充协议。"
    )
    document = Document()
    document.add_heading("某市政务云运维项目施工合同", level=1)
    document.add_paragraph("第一条 合同价格")
    document.add_paragraph(f"本合同总价为人民币 620 万元。{filler}")
    document.add_paragraph("第二条 付款方式")
    document.add_paragraph("发包人待业主支付后向乙方支付合同款项。")
    document.add_paragraph("第三条 违约责任")
    document.add_paragraph(f"乙方违约的，应按合同总价的 30% 支付违约金。{filler}")
    document.add_paragraph("第四条 验收")
    document.add_paragraph("项目完成后组织验收。")
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


async def _seed_constraint(tenant_id: UUID, project_id: UUID) -> None:
    payload = {
        "budget": {"amount": "5000000", "currency": "CNY"},
        "substantive_requirements": [
            {"text": "投标人须提供原厂授权及售后服务承诺书"},
        ],
        "timeline": [],
    }
    async with SessionLocal() as session:
        document = TenderDocument(
            tenant_id=tenant_id,
            project_id=project_id,
            filename="招标文件.docx",
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            size_bytes=1024,
            file_hash=uuid4().hex,
            object_ref="test://tender-fixture",
            parse_status="parsed",
        )
        session.add(document)
        await session.flush()
        session.add(
            TenderConstraint(
                tenant_id=tenant_id,
                project_id=project_id,
                document_id=document.id,
                version=1,
                payload=payload,
                parse_meta={"parser_version": "rules-v1.2"},
            )
        )
        await session.commit()


async def _cleanup_project(tenant_id: UUID, project_id: UUID) -> None:
    async with SessionLocal() as session:
        await session.execute(
            delete(ContractFinding).where(ContractFinding.tenant_id == tenant_id)
        )
        await session.execute(
            delete(TenderConstraint).where(TenderConstraint.project_id == project_id)
        )
        await session.commit()


@pytest.mark.integration
async def test_contract_review_full_flow() -> None:
    tenant_id, _, email = await seed_tenant_user("contract-a")
    other_tenant_id, _, other_email = await seed_tenant_user("contract-b")
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = await login(client, email)
            project = await client.post(
                "/api/v1/projects",
                json={"name": "合同审查项目"},
                headers=headers,
            )
            assert project.status_code == 201, project.text
            project_id = project.json()["id"]
            await _seed_constraint(tenant_id, UUID(project_id))

            uploaded = await client.post(
                f"/api/v1/projects/{project_id}/contract-reviews",
                files={"file": ("施工合同.docx", _contract_docx(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
                headers=headers,
            )
            assert uploaded.status_code == 200, uploaded.text
            body = uploaded.json()
            assert body["disclaimer"].startswith("规则与启发式审查结果")
            assert body["summary_json"]["clauses"] >= 5
            # 审查点模板自动匹配：关键词命中或 general 兜底，matched 必非空
            assert body["template_matches"]["matched"], body["template_matches"]
            review_id = body["id"]

            rule_ids = {finding["rule_id"] for finding in body["findings"]}
            kinds = {finding["kind"] for finding in body["findings"]}
            assert "back_to_back_payment" in rule_ids
            assert "penalty_cap_missing" in rule_ids
            assert "confidentiality_missing" in rule_ids
            assert "deviation" in kinds
            deviation = next(f for f in body["findings"] if f["kind"] == "deviation")
            assert deviation["severity"] == "high"
            assert "第四十六条" in (deviation["legal_basis"] or "")

            # 确认流：确认一条 → confirmed_by 落库
            target = body["findings"][0]
            confirmed = await client.patch(
                f"/api/v1/projects/{project_id}/contract-reviews/findings/{target['id']}",
                json={"status": "confirmed"},
                headers=headers,
            )
            assert confirmed.status_code == 200, confirmed.text
            updated = next(
                f for f in confirmed.json()["findings"] if f["id"] == target["id"]
            )
            assert updated["status"] == "confirmed"
            assert updated["confirmed_by"] is not None
            assert updated["confirmed_at"] is not None

            detail = await client.get(
                f"/api/v1/projects/{project_id}/contract-reviews/{review_id}",
                headers=headers,
            )
            assert detail.status_code == 200
            assert detail.json()["disclaimer"]

            exported = await client.get(
                f"/api/v1/projects/{project_id}/contract-reviews/{review_id}/export",
                headers=headers,
            )
            assert exported.status_code == 200
            assert len(exported.content) > 500
            workbook = load_workbook(BytesIO(exported.content))
            sheet = workbook.active
            assert sheet.max_row >= 2  # 表头 + 至少一条 findings

            # 跨租户：其他租户不可见
            other_headers = await login(client, other_email)
            other_project = await client.post(
                "/api/v1/projects",
                json={"name": "其他租户项目"},
                headers=other_headers,
            )
            other_project_id = other_project.json()["id"]
            forbidden = await client.get(
                f"/api/v1/projects/{other_project_id}/contract-reviews/{review_id}",
                headers=other_headers,
            )
            assert forbidden.status_code == 404
            forbidden_patch = await client.patch(
                f"/api/v1/projects/{other_project_id}/contract-reviews/findings/{target['id']}",
                json={"status": "dismissed"},
                headers=other_headers,
            )
            assert forbidden_patch.status_code == 404

            # 不支持的后缀
            bad = await client.post(
                f"/api/v1/projects/{project_id}/contract-reviews",
                files={"file": ("合同.zip", b"PK", "application/zip")},
                headers=headers,
            )
            assert bad.status_code == 422
    finally:
        app.dependency_overrides.clear()
        async with SessionLocal() as session:
            (
                await session.execute(
                    select(ContractFinding.review_id).where(
                        ContractFinding.tenant_id == tenant_id
                    )
                )
            ).scalars().all()
        await cleanup_tenants([tenant_id, other_tenant_id])


@pytest.mark.integration
async def test_contract_review_without_constraint_skips_consistency() -> None:
    tenant_id, _, email = await seed_tenant_user("contract-c")
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = await login(client, email)
            project = await client.post(
                "/api/v1/projects",
                json={"name": "无约束集合同"},
                headers=headers,
            )
            project_id = project.json()["id"]
            uploaded = await client.post(
                f"/api/v1/projects/{project_id}/contract-reviews",
                files={"file": ("合同.txt", "第一条 合同价格\n本合同总价为人民币 100 万元。".encode(), "text/plain")},
                headers=headers,
            )
            assert uploaded.status_code == 200, uploaded.text
            assert uploaded.json()["summary_json"]["skipped_consistency"] is True
    finally:
        await cleanup_tenants([tenant_id])


class _FakeLLM:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls = 0

    def configured(self) -> bool:
        return True

    async def complete(self, *, system: str, user: str) -> str:
        self.calls += 1
        return self.content


class _UnconfiguredLLM:
    def configured(self) -> bool:
        return False


@pytest.mark.integration
async def test_contract_deep_review_records_cost_and_requires_evidence(monkeypatch) -> None:
    from app.modules.costs.models import CostLedger

    tenant_id, _, email = await seed_tenant_user("contract-deep")
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = await login(client, email)
            project = await client.post(
                "/api/v1/projects", json={"name": "深审项目"}, headers=headers
            )
            project_id = project.json()["id"]
            uploaded = await client.post(
                f"/api/v1/projects/{project_id}/contract-reviews",
                files={"file": ("合同.txt", "第一条 违约责任\n乙方违约应支付违约金。".encode(), "text/plain")},
                headers=headers,
            )
            assert uploaded.status_code == 200, uploaded.text
            review_id = uploaded.json()["id"]

            fake_llm = _FakeLLM(
                '{"findings": ['
                '{"clause_seq": 1, "kind": "risk", "severity": "high", '
                '"message": "违约金比例过高", "legal_basis": "民法典违约责任", "evidence": "应支付违约金"},'
                '{"clause_seq": 1, "kind": "risk", "severity": "low", '
                '"message": "无证据结论", "legal_basis": null, "evidence": ""}'
                "]}",
            )
            app.dependency_overrides[get_llm_client] = lambda: fake_llm
            deep = await client.post(
                f"/api/v1/projects/{project_id}/contract-reviews/{review_id}/deep-review",
                headers=headers,
            )
            assert deep.status_code == 200, deep.text
            llm_findings = [
                f for f in deep.json()["findings"] if f["source"] == "llm"
            ]
            assert len(llm_findings) == 1  # 无证据的被丢弃
            assert "违约金比例过高" in llm_findings[0]["message"]
            assert fake_llm.calls == 1

            async with SessionLocal() as session:
                ledger_rows = (
                    await session.execute(
                        select(CostLedger).where(
                            CostLedger.tenant_id == tenant_id,
                            CostLedger.operation == "contract.deep_review",
                        )
                    )
                ).scalars().all()
                assert ledger_rows and ledger_rows[0].charged is True

            app.dependency_overrides[get_llm_client] = lambda: _UnconfiguredLLM()
            unconfigured = await client.post(
                f"/api/v1/projects/{project_id}/contract-reviews/{review_id}/deep-review",
                headers=headers,
            )
            assert unconfigured.status_code == 422
    finally:
        app.dependency_overrides.clear()
        await cleanup_tenants([tenant_id])


@pytest.mark.integration
async def test_contract_ops_milestones_tenant_rules_supplement() -> None:
    tenant_id, _, email = await seed_tenant_user("contract-ops")
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = await login(client, email)
            project = await client.post(
                "/api/v1/projects", json={"name": "合同运营项目"}, headers=headers
            )
            project_id = project.json()["id"]

            # 租户自定义规则
            rule = await client.post(
                f"/api/v1/projects/{project_id}/contract-reviews/tenant-rules",
                json={
                    "name": "禁止转包",
                    "pattern": r"转包|违法分包",
                    "severity": "high",
                    "message_template": "发现转包/违法分包措辞，需法务专项确认",
                },
                headers=headers,
            )
            assert rule.status_code == 200, rule.text

            # 租户自定义规则必须能读回来：GET 曾因 /{review_id} 路由先注册而被遮蔽成 422
            listed_rules = await client.get(
                f"/api/v1/projects/{project_id}/contract-reviews/tenant-rules",
                headers=headers,
            )
            assert listed_rules.status_code == 200, listed_rules.text
            assert any(row["pattern"] == r"转包|违法分包" for row in listed_rules.json())

            # 上传含付款日期 + 转包措辞的主合同
            main_docx = BytesIO()
            doc = Document()
            doc.add_paragraph("第一条 付款方式")
            doc.add_paragraph("甲方应于 2027年3月15日 前支付进度款 100 万元。")
            doc.add_paragraph("第二条 分包约束")
            doc.add_paragraph("乙方不得将工程转包或违法分包。")
            doc.save(main_docx)
            uploaded = await client.post(
                f"/api/v1/projects/{project_id}/contract-reviews",
                files={"file": ("主合同.docx", main_docx.getvalue(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
                headers=headers,
            )
            assert uploaded.status_code == 200, uploaded.text
            review_id = uploaded.json()["id"]
            rule_ids = {f["rule_id"] for f in uploaded.json()["findings"]}
            assert "tenant:禁止转包" in rule_ids

            milestones = await client.get(
                f"/api/v1/projects/{project_id}/contract-reviews/{review_id}/milestones",
                headers=headers,
            )
            assert milestones.status_code == 200
            ms_rows = milestones.json()
            assert any(row["kind"] == "payment" and row["due_at"] for row in ms_rows)

            # 非法正则被拒
            bad_rule = await client.post(
                f"/api/v1/projects/{project_id}/contract-reviews/tenant-rules",
                json={"name": "坏正则", "pattern": r"([", "severity": "low", "message_template": "x"},
                headers=headers,
            )
            assert bad_rule.status_code == 422

            # 补充协议：改第一条 + 新增保密条款
            supp_docx = BytesIO()
            supp = Document()
            supp.add_paragraph("第一条 付款方式")
            supp.add_paragraph("甲方应于 2027年6月30日 前支付进度款 120 万元。")
            supp.add_paragraph("第三条 保密义务")
            supp.add_paragraph("双方对履约过程中知悉的商业秘密承担保密义务。")
            supp.save(supp_docx)
            supplement = await client.post(
                f"/api/v1/projects/{project_id}/contract-reviews/{review_id}/supplements",
                files={"file": ("补充协议.docx", supp_docx.getvalue(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
                headers=headers,
            )
            assert supplement.status_code == 200, supplement.text
            supp_rule_ids = {f["rule_id"] for f in supplement.json()["findings"]}
            assert {"supplement_changed_clause", "supplement_new_clause"} <= supp_rule_ids
            changed = next(
                f for f in supplement.json()["findings"]
                if f["rule_id"] == "supplement_changed_clause"
            )
            assert "第四十六条" in (changed["legal_basis"] or "")
    finally:
        app.dependency_overrides.clear()
        await cleanup_tenants([tenant_id])
