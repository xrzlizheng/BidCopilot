"""合同审查升级件集成测试：审查点模板、深审分批与部分失败、法条注入与
引用校验、修订对照、扫描件 OCR 兜底。

依赖真实 PostgreSQL + 已迁移 schema（law_articles 的种子随 0070 迁移写入）。
LLM/OCR 一律 dependency_overrides 打桩（生产禁 mock，测试替身只进 backend/tests）。
"""

import json
from io import BytesIO

import pytest
from docx import Document
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.modules.generation.deps import get_llm_client
from app.modules.tender.deps import get_ocr_provider
from app.modules.tender.ocr import OcrPage, OcrResult
from tests.support import cleanup_tenants, login, seed_tenant_user

_FILLER = (
    "双方经友好协商，就本项目达成如下条款，明确双方权利义务与履行安排；"
    "本合同经双方签署后生效，对双方均具有法律约束力，双方应共同遵守执行，"
    "未尽事宜由双方另行协商并签订书面补充协议。"
)


def _multi_clause_contract_txt(clauses: int = 10, size: int = 1400) -> str:
    """多长条款合同：超过默认 6000 字符批预算，触发分批深审。

    条款正文含「付款/违约金」关键词，保证深审法条召回有命中（585/626）；
    第 5 条带「故障标记条」标题，供内容感知 LLM 桩精准注入该批失败。
    """
    lines = ["某信息系统集成合同", ""]
    for index in range(1, clauses + 1):
        marker = "故障标记条" if index == 5 else "服务内容与履行安排"
        lines.append(f"第{index}条 {marker}")
        lines.append(
            f"本条约定的服务内容为系统部署、联调、培训、付款节点与违约金计算等事项（{index}）。"
        )
        lines.append("双方应按照附件约定的范围与进度完成各项工作，并及时书面确认。" * (size // 30))
        lines.append("")
    return "\n".join(lines)


class _ContentAwareLLM:
    """内容感知桩：按 prompt 中的条款**文本**定位目标条款（不依赖 seq 编号，
    前言会占据 seq=1），含「故障标记条」的批次输出坏 JSON。并发下无竞态。"""

    def __init__(self, *, model: str = "stub-model") -> None:
        self.calls = 0
        self.prompts: list[str] = []
        self.model = model

    def configured(self) -> bool:
        return True

    async def complete(self, *, system: str, user: str) -> str:
        import re as _re

        self.calls += 1
        self.prompts.append(f"{system}\n{user}")
        if "故障标记条" in user:
            return "不是 JSON"
        blocks = _re.findall(r"\[(\d+)\|[^\]]*\]([^\[]*)", user)
        if "某信息系统集成合同" in user:
            target = next(block for block in blocks if "违约金计算" in block[1])
            return _finding_json(int(target[0]), "违约金比例过高", "违约金计算")
        target = next(block for block in blocks if "付款节点" in block[1])
        return _finding_json(int(target[0]), "付款节点缺失", "付款节点与违约金计算")


class _BulkLLM:
    """单批返回 60 条 finding：验证跨批总量截断与 truncated 显式标记（backlog B1）。"""

    def __init__(self, *, model: str = "stub-model") -> None:
        self.model = model
        self.calls = 0

    def configured(self) -> bool:
        return True

    async def complete(self, *, system: str, user: str) -> str:
        import re as _re

        self.calls += 1
        seqs = _re.findall(r"\[(\d+)\|", user)
        seq = int(seqs[0]) if seqs else 1
        findings = [
            {
                "clause_seq": seq,
                "kind": "risk",
                "severity": "low",
                "message": f"批量结论 {index}",
                "legal_basis": None,
                "evidence": f"第{seq}条",
            }
            for index in range(60)
        ]
        return json.dumps({"findings": findings}, ensure_ascii=False)


class _UnconfiguredLLM:
    def configured(self) -> bool:
        return False


class _AlwaysBadLLM:
    """所有批次都输出非 JSON：全批失败 → 503。"""

    def __init__(self, *, model: str = "stub-model") -> None:
        self.model = model
        self.calls = 0

    def configured(self) -> bool:
        return True

    async def complete(self, *, system: str, user: str) -> str:
        self.calls += 1
        return "坏输出"


def _finding_json(clause_seq: int, message: str, evidence: str) -> str:
    return json.dumps(
        {
            "findings": [
                {
                    "clause_seq": clause_seq,
                    "kind": "risk",
                    "severity": "high",
                    "message": message,
                    "legal_basis": "《中华人民共和国民法典》第五百八十五条",
                    "evidence": evidence,
                    "redline": "违约金累计不超过合同总价的 10%。",
                }
            ]
        },
        ensure_ascii=False,
    )


class _StubOcrProvider:
    name = "stub"

    async def recognize(self, content: bytes, filename: str, *, max_pages: int) -> OcrResult:
        text = "\n".join(
            [
                "某扫描版设备维护合同",
                "第一条 付款方式",
                "甲方按合同金额的 100% 比例于验收后支付。" + _FILLER,
                "第二条 质保",
                "乙方提供 2 年质保，质保期内免费维修。",
            ]
        )
        return OcrResult(pages=[OcrPage(page_no=1, text=text)], tables=[], provider="stub", structure_used=False)


async def _create_project(client: AsyncClient, headers: dict, name: str) -> str:
    project = await client.post("/api/v1/projects", json={"name": name}, headers=headers)
    assert project.status_code == 201, project.text
    return project.json()["id"]


@pytest.mark.integration
async def test_review_templates_listing_and_manual_override() -> None:
    tenant_id, _, email = await seed_tenant_user("contract-tpl")
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = await login(client, email)
            project_id = await _create_project(client, headers, "模板目录项目")

            listed = await client.get(
                f"/api/v1/projects/{project_id}/contract-reviews/review-templates",
                headers=headers,
            )
            assert listed.status_code == 200, listed.text
            templates = listed.json()
            assert len(templates) >= 25, "系统种子模板应随 0069 迁移写入"
            assert all(row["is_system"] for row in templates)
            general = next(row for row in templates if row["contract_type"] == "general")
            assert general["review_points"], "general 兜底模板必须自带审查点"

            # 显式指定模板 + 补充关注点 + 核心目的
            chosen = templates[0]["id"]
            uploaded = await client.post(
                f"/api/v1/projects/{project_id}/contract-reviews",
                files={
                    "file": (
                        "合同.txt",
                        f"第一条 付款方式\n甲方按 100% 比例支付。{_FILLER}".encode(),
                        "text/plain",
                    )
                },
                data={
                    "template_ids": chosen,
                    "review_points": "关注垫资条款\n关注背靠背支付",
                    "core_purpose": "重点核查付款对等性",
                },
                headers=headers,
            )
            assert uploaded.status_code == 200, uploaded.text
            match = uploaded.json()["template_matches"]
            assert match["matched"][0]["basis"] == "manual"
            assert match["matched"][0]["template_id"] == chosen
            assert match["applied_review_points"][:2] == ["关注垫资条款", "关注背靠背支付"]
            assert match["core_purposes"] == ["重点核查付款对等性"]
            assert uploaded.json()["summary_json"]["review_points"] >= 2
    finally:
        app.dependency_overrides.clear()
        await cleanup_tenants([tenant_id])


@pytest.mark.integration
async def test_deep_review_batching_partial_failure_and_quote_verification() -> None:
    tenant_id, _, email = await seed_tenant_user("contract-batch")
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = await login(client, email)
            project_id = await _create_project(client, headers, "分批深审项目")
            content = _multi_clause_contract_txt().encode()
            uploaded = await client.post(
                f"/api/v1/projects/{project_id}/contract-reviews",
                files={"file": ("长合同.txt", content, "text/plain")},
                headers=headers,
            )
            assert uploaded.status_code == 200, uploaded.text
            review_id = uploaded.json()["id"]

            # 内容感知桩：含故障标记条的批次连修复重试也坏 → 恰好一批失败
            fake = _ContentAwareLLM()
            app.dependency_overrides[get_llm_client] = lambda: fake
            deep = await client.post(
                f"/api/v1/projects/{project_id}/contract-reviews/{review_id}/deep-review",
                headers=headers,
            )
            assert deep.status_code == 200, deep.text
            body = deep.json()
            batches = body["summary_json"]["llm_batches"]
            assert batches["total"] == 3
            assert batches["failed"] == 1
            assert batches["errors"] and batches["errors"][0].startswith("batch#")
            llm_findings = [f for f in body["findings"] if f["source"] == "llm"]
            assert len(llm_findings) == 2
            messages = {f["message"] for f in llm_findings}
            assert messages == {"违约金比例过高", "付款节点缺失"}
            # created_at 同秒时 findings 顺序不稳定，按消息选取而非下标
            finding = next(f for f in llm_findings if f["message"] == "违约金比例过高")
            # 证据逐字回查命中（evidence 来自对应条款原文）
            assert finding["evidence_span"]["quote_verified"] is True
            assert finding["evidence_span"]["char_start"] >= 0
            # 法条引用反查 0070 种子库（民法典 585 在列）
            assert finding["evidence_span"]["legal_basis_verified"] is True, json.dumps(
                [
                    {"msg": f["message"], "legal_basis": f["legal_basis"], "span": f["evidence_span"]}
                    for f in llm_findings
                ],
                ensure_ascii=False,
            )
            assert finding["confidence"] in ("0.60", 0.6, "0.6")
            assert finding["detection_method"] == "llm"
            assert finding["redline"].startswith("违约金累计不超过")
            # 法条召回块注入了批次 prompt（条款含"违约金"→ 585 命中）
            assert any("可用法条" in prompt for prompt in fake.prompts)

            # 全部批次失败 → 503（不静默降级为空列表）
            app.dependency_overrides[get_llm_client] = lambda: _AlwaysBadLLM()
            deep_fail = await client.post(
                f"/api/v1/projects/{project_id}/contract-reviews/{review_id}/deep-review",
                headers=headers,
            )
            assert deep_fail.status_code == 503

            app.dependency_overrides[get_llm_client] = lambda: _UnconfiguredLLM()
            unconfigured = await client.post(
                f"/api/v1/projects/{project_id}/contract-reviews/{review_id}/deep-review",
                headers=headers,
            )
            assert unconfigured.status_code == 422
    finally:
        app.dependency_overrides.clear()
        await cleanup_tenants([tenant_id])


def _v1_docx() -> bytes:
    document = Document()
    document.add_heading("某设备维护合同", level=1)
    document.add_paragraph("第一条 付款方式")
    document.add_paragraph(f"甲方按合同金额的 100% 比例支付。{_FILLER}")
    document.add_paragraph("第二条 违约责任")
    document.add_paragraph(f"乙方违约的，应支付违约金，累计不超过合同总价的 10%。{_FILLER}")
    document.add_paragraph("第三条 验收")
    document.add_paragraph("按国家规范及约定的 SLA 指标验收，可用率不低于 99.9%。")
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _v2_docx() -> bytes:
    document = Document()
    document.add_heading("某设备维护合同（修订版）", level=1)
    document.add_paragraph("第一条 付款方式")
    document.add_paragraph(f"甲方按合同金额的 100% 比例支付。{_FILLER}")
    document.add_paragraph("第二条 违约责任")
    document.add_paragraph(f"乙方违约的，应支付违约金，累计不超过合同总价的 10%。{_FILLER}")
    document.add_paragraph("第三条 验收")
    document.add_paragraph("按国家规范及约定的 SLA 指标验收，可用率不低于 99.9%。")
    document.add_paragraph("第四条 保密义务")
    document.add_paragraph("双方对履约过程中知悉的商业秘密承担保密义务。")
    document.add_paragraph("第五条 知识产权")
    document.add_paragraph("交付成果的知识产权归甲方所有。")
    document.add_paragraph("第六条 其他")
    document.add_paragraph("补充交付地点为____元整待补。")
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


@pytest.mark.integration
async def test_re_review_diff_added_resolved() -> None:
    tenant_id, _, email = await seed_tenant_user("contract-rereview")
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = await login(client, email)
            project_id = await _create_project(client, headers, "修订对照项目")
            # python-docx 每次序列化会写入当前时间戳，字节不稳定：
            # 同文件 422 断言必须复用同一份字节。
            v1_bytes = _v1_docx()
            first = await client.post(
                f"/api/v1/projects/{project_id}/contract-reviews",
                files={"file": ("v1.docx", v1_bytes, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
                headers=headers,
            )
            assert first.status_code == 200, first.text
            v1 = first.json()
            old_rule_ids = {f["rule_id"] for f in v1["findings"]}
            assert {"confidentiality_missing", "ip_missing"} <= old_rule_ids

            # 同文件重审 → 明确 422（无变更可对照）
            same = await client.post(
                f"/api/v1/projects/{project_id}/contract-reviews/{v1['id']}/re-review",
                files={"file": ("v1-again.docx", v1_bytes, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
                headers=headers,
            )
            assert same.status_code == 422

            second = await client.post(
                f"/api/v1/projects/{project_id}/contract-reviews/{v1['id']}/re-review",
                files={"file": ("v2.docx", _v2_docx(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
                headers=headers,
            )
            assert second.status_code == 200, second.text
            v2 = second.json()
            assert v2["replaces_review_id"] == v1["id"]
            diff = v2["summary_json"]["revision_diff"]
            resolved_ids = {item["rule_id"] for item in diff["resolved"]}
            added_ids = {item["rule_id"] for item in diff["added"]}
            assert {"confidentiality_missing", "ip_missing"} <= resolved_ids
            assert "placeholder_blank_fields" in added_ids
            # 新审查自身也落库了对应 finding（确认流继续生效）
            new_rule_ids = {f["rule_id"] for f in v2["findings"]}
            assert "placeholder_blank_fields" in new_rule_ids
            assert "confidentiality_missing" not in new_rule_ids
    finally:
        app.dependency_overrides.clear()
        await cleanup_tenants([tenant_id])


@pytest.mark.integration
async def test_scanned_contract_uses_ocr_or_fails_explicitly() -> None:
    tenant_id, _, email = await seed_tenant_user("contract-ocr")
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = await login(client, email)
            project_id = await _create_project(client, headers, "扫描件项目")

            # 无 OCR 通道：图片合同显式 422（不是模糊 503）
            app.dependency_overrides[get_ocr_provider] = lambda: None
            denied = await client.post(
                f"/api/v1/projects/{project_id}/contract-reviews",
                files={"file": ("扫描合同.png", b"\x89PNG fake", "image/png")},
                headers=headers,
            )
            assert denied.status_code == 422, denied.text
            assert "OCR" in denied.text

            # 有 OCR 通道：图片走 OCR → 条款切分与规则照常执行
            app.dependency_overrides[get_ocr_provider] = lambda: _StubOcrProvider()
            ok = await client.post(
                f"/api/v1/projects/{project_id}/contract-reviews",
                files={"file": ("扫描合同.png", b"\x89PNG fake", "image/png")},
                headers=headers,
            )
            assert ok.status_code == 200, ok.text
            body = ok.json()
            assert body["summary_json"]["ocr"] == {"provider": "stub", "used": True}
            assert body["summary_json"]["clauses"] >= 2
    finally:
        app.dependency_overrides.clear()
        await cleanup_tenants([tenant_id])


@pytest.mark.integration
async def test_deep_review_truncation_flagged_not_silent() -> None:
    """backlog B1：跨批总量超 50 截断时 summary 显式标记，不静默丢数据。"""
    tenant_id, _, email = await seed_tenant_user("contract-trunc")
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = await login(client, email)
            project_id = await _create_project(client, headers, "截断标记项目")
            uploaded = await client.post(
                f"/api/v1/projects/{project_id}/contract-reviews",
                files={
                    "file": (
                        "合同.txt",
                        f"第一条 付款\n甲方按 100% 比例支付。{_FILLER}".encode(),
                        "text/plain",
                    )
                },
                headers=headers,
            )
            assert uploaded.status_code == 200, uploaded.text
            review_id = uploaded.json()["id"]

            app.dependency_overrides[get_llm_client] = lambda: _BulkLLM()
            deep = await client.post(
                f"/api/v1/projects/{project_id}/contract-reviews/{review_id}/deep-review",
                headers=headers,
            )
            assert deep.status_code == 200, deep.text
            body = deep.json()
            batches = body["summary_json"]["llm_batches"]
            assert batches["truncated"] is True
            assert batches["truncated_count"] == 10  # 60 - 50
            llm_findings = [f for f in body["findings"] if f["source"] == "llm"]
            assert len(llm_findings) == 50
    finally:
        app.dependency_overrides.clear()
        await cleanup_tenants([tenant_id])


@pytest.mark.integration
async def test_template_crud_with_versions_and_tenant_boundary() -> None:
    """backlog B7：租户模板 CRUD 全周期 + 版本快照 + 系统模板不可变 + 租户边界。"""
    tenant_id, _, email = await seed_tenant_user("contract-tplcrud")
    other_tenant_id, _, other_email = await seed_tenant_user("contract-tplcrud-b")
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = await login(client, email)
            await _create_project(client, headers, "模板CRUD项目")

            created = await client.post(
                "/api/v1/projects/00000000-0000-0000-0000-000000000000/contract-reviews/review-templates",
                json={
                    "contract_type": "my_trade",
                    "name": "本租户贸易模板",
                    "review_points": ["核查贸易术语", "核查汇率风险"],
                    "prompt_rules": ["不得建议违法条款"],
                },
                headers=headers,
            )
            assert created.status_code == 200, created.text
            template = created.json()
            assert template["is_system"] is False
            template_id = template["id"]

            # contract_type 租户内唯一
            dup = await client.post(
                "/api/v1/projects/00000000-0000-0000-0000-000000000000/contract-reviews/review-templates",
                json={"contract_type": "my_trade", "name": "重复", "review_points": ["x"]},
                headers=headers,
            )
            assert dup.status_code == 409

            # 非法 contract_type → 422
            bad = await client.post(
                "/api/v1/projects/00000000-0000-0000-0000-000000000000/contract-reviews/review-templates",
                json={"contract_type": "大写非法", "name": "x", "review_points": ["x"]},
                headers=headers,
            )
            assert bad.status_code == 422

            # PATCH → 快照 v2
            updated = await client.patch(
                f"/api/v1/projects/00000000-0000-0000-0000-000000000000/contract-reviews/review-templates/{template_id}",
                json={"contract_type": "my_trade", "name": "本租户贸易模板 v2", "review_points": ["核查贸易术语"]},
                headers=headers,
            )
            assert updated.status_code == 200, updated.text

            # 空审查点 → 422
            empty_points = await client.patch(
                f"/api/v1/projects/00000000-0000-0000-0000-000000000000/contract-reviews/review-templates/{template_id}",
                json={"contract_type": "my_trade", "name": "x", "review_points": []},
                headers=headers,
            )
            assert empty_points.status_code == 422

            # 版本列表：v2/v1（desc），v1 快照含创建时的两条审查点
            versions = await client.get(
                f"/api/v1/projects/00000000-0000-0000-0000-000000000000/contract-reviews/review-templates/{template_id}/versions",
                headers=headers,
            )
            assert versions.status_code == 200, versions.text
            rows = versions.json()
            assert [row["version"] for row in rows] == [2, 1]
            assert len(rows[1]["snapshot"]["review_points"]) == 2

            # 回退 v1 → 当前内容恢复两条审查点，并产生 v3 快照
            reverted = await client.post(
                f"/api/v1/projects/00000000-0000-0000-0000-000000000000/contract-reviews/review-templates/{template_id}/revert/1",
                headers=headers,
            )
            assert reverted.status_code == 200, reverted.text
            assert len(reverted.json()["review_points"]) == 2
            after_revert = await client.get(
                f"/api/v1/projects/00000000-0000-0000-0000-000000000000/contract-reviews/review-templates/{template_id}/versions",
                headers=headers,
            )
            assert [row["version"] for row in after_revert.json()] == [3, 2, 1]

            # 回退不存在的版本 → 404
            missing_version = await client.post(
                f"/api/v1/projects/00000000-0000-0000-0000-000000000000/contract-reviews/review-templates/{template_id}/revert/99",
                headers=headers,
            )
            assert missing_version.status_code == 404

            # 其他租户：不可见、不可改（scoped 过滤 → 404）
            other_headers = await login(client, other_email)
            other_read = await client.get(
                f"/api/v1/projects/00000000-0000-0000-0000-000000000000/contract-reviews/review-templates/{template_id}/versions",
                headers=other_headers,
            )
            assert other_read.status_code == 404
            other_patch = await client.patch(
                f"/api/v1/projects/00000000-0000-0000-0000-000000000000/contract-reviews/review-templates/{template_id}",
                json={"contract_type": "my_trade", "name": "越权", "review_points": ["x"]},
                headers=other_headers,
            )
            assert other_patch.status_code == 404

            # 系统模板不可变：scoped 过滤掉 tenant_id 为空的系统行 → 404
            listed = await client.get(
                "/api/v1/projects/00000000-0000-0000-0000-000000000000/contract-reviews/review-templates",
                headers=headers,
            )
            system_id = next(row["id"] for row in listed.json() if row["is_system"])
            system_patch = await client.patch(
                f"/api/v1/projects/00000000-0000-0000-0000-000000000000/contract-reviews/review-templates/{system_id}",
                json={"contract_type": "labor", "name": "改系统模板", "review_points": ["x"]},
                headers=headers,
            )
            assert system_patch.status_code == 404

            # 删除：版本随之清理
            deleted = await client.delete(
                f"/api/v1/projects/00000000-0000-0000-0000-000000000000/contract-reviews/review-templates/{template_id}",
                headers=headers,
            )
            assert deleted.status_code == 200
            versions_gone = await client.get(
                f"/api/v1/projects/00000000-0000-0000-0000-000000000000/contract-reviews/review-templates/{template_id}/versions",
                headers=headers,
            )
            assert versions_gone.status_code == 404
    finally:
        app.dependency_overrides.clear()
        await cleanup_tenants([tenant_id, other_tenant_id])


@pytest.mark.integration
async def test_template_revert_type_conflict_returns_409() -> None:
    """review-2 P1 回归：revert 恢复的 contract_type 已被其他模板占用 → 409，不是 500。"""
    tenant_id, _, email = await seed_tenant_user("contract-revert409")
    try:
        base = "/api/v1/projects/00000000-0000-0000-0000-000000000000/contract-reviews/review-templates"
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = await login(client, email)

            t1 = await client.post(
                base,
                json={"contract_type": "revert_a", "name": "T1", "review_points": ["p1"]},
                headers=headers,
            )
            assert t1.status_code == 200, t1.text
            t1_id = t1.json()["id"]

            # T1 改为 revert_b（v2 快照记录 revert_a）
            patched = await client.patch(
                f"{base}/{t1_id}",
                json={"contract_type": "revert_b", "name": "T1-b", "review_points": ["p1"]},
                headers=headers,
            )
            assert patched.status_code == 200, patched.text

            # T2 占用 revert_a
            t2 = await client.post(
                base,
                json={"contract_type": "revert_a", "name": "T2", "review_points": ["p2"]},
                headers=headers,
            )
            assert t2.status_code == 200, t2.text
            t2_id = t2.json()["id"]

            # revert T1 → v1（revert_a）撞 T2 的类型：409 + 明确文案，且 T1 未被改动
            conflict = await client.post(
                f"{base}/{t1_id}/revert/1",
                headers=headers,
            )
            assert conflict.status_code == 409, conflict.text
            assert "contract_type" in conflict.text

            # 数据未损坏：T1 仍是 revert_b，版本数不变；T2 完好
            versions = await client.get(f"{base}/{t1_id}/versions", headers=headers)
            assert [row["version"] for row in versions.json()] == [2, 1]
            current = await client.get(base, headers=headers)
            types = {row["contract_type"]: row["id"] for row in current.json() if not row["is_system"]}
            assert types.get("revert_b") == t1_id
            assert types.get("revert_a") == t2_id

            # 清理
            await client.delete(f"{base}/{t2_id}", headers=headers)
            await client.delete(f"{base}/{t1_id}", headers=headers)
    finally:
        app.dependency_overrides.clear()
        await cleanup_tenants([tenant_id])


@pytest.mark.integration
async def test_upload_rejects_foreign_project_explicitly() -> None:
    """backlog B4：跨租户向他人项目上传合同 → 404（不泄露存在性）。"""
    tenant_id, _, email = await seed_tenant_user("contract-own-a")
    other_tenant_id, _, other_email = await seed_tenant_user("contract-own-b")
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = await login(client, email)
            project_id = await _create_project(client, headers, "归属校验项目")

            other_headers = await login(client, other_email)
            foreign = await client.post(
                f"/api/v1/projects/{project_id}/contract-reviews",
                files={"file": ("合同.txt", "第一条 付款\n按比例支付。".encode(), "text/plain")},
                headers=other_headers,
            )
            assert foreign.status_code == 404, foreign.text
    finally:
        app.dependency_overrides.clear()
        await cleanup_tenants([tenant_id, other_tenant_id])
 
