from io import BytesIO
from uuid import uuid4

from httpx import ASGITransport, AsyncClient
from openpyxl import load_workbook  # type: ignore[import-untyped]

from app.main import app
from tests.integration.test_projects_api import _seed_tenant

TENDER = """
某市政务云运维服务项目招标文件
项目预算：人民币 150 万元。
投标人须具备有效的营业执照及 CMMI3 级及以上资质。
投标人须提供近三年经审计的财务报告。
所投产品须提供原厂授权书。
系统应支持双活架构，全年可用性不低于 99.95%。
服务期内须提供 7x24 小时响应，故障响应时间不超过 30 分钟。
评分采用综合评分法：
1. 技术方案：40 分
2. 投标报价：60 分
未按招标文件要求密封的投标将被否决。
"""


async def _setup(label: str) -> tuple[AsyncClient, dict, str]:
    _, email = await _seed_tenant(label)
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    login = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": "Password123!"}
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    project = await client.post("/api/v1/projects", json={"name": "响应矩阵项目"}, headers=headers)
    project_id = project.json()["id"]
    uploaded = await client.post(
        f"/api/v1/projects/{project_id}/documents",
        files={"file": ("tender.txt", TENDER.encode("utf-8"), "text/plain")},
        headers=headers,
    )
    assert uploaded.status_code == 200, uploaded.text
    return client, headers, project_id


async def test_response_matrix_build_confirm_and_rebuild() -> None:
    client, headers, project_id = await _setup("responses-a")
    try:
        built = await client.post(
            f"/api/v1/projects/{project_id}/responses/build", headers=headers
        )
        assert built.status_code == 200, built.text
        body = built.json()
        assert body["total"] >= 4, body
        assert body["summary"]["pending"] == body["total"]
        assert body["unresolved"] == body["total"]

        types = {item["requirement_type"] for item in body["items"]}
        assert {"qualification", "substantive", "scoring"} <= types, types

        target = next(item for item in body["items"] if item["requirement_type"] == "qualification")
        patched = await client.patch(
            f"/api/v1/projects/{project_id}/responses/{target['id']}",
            json={
                "response_status": "responded",
                "response_note": "已在技术方案第三章响应",
                "evidence_refs": ["产品白皮书 v3"],
                "section_ref": "技术方案-3",
            },
            headers=headers,
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["response_status"] == "responded"
        assert patched.json()["confirmed_by"] is not None

        rescoped = await client.patch(
            f"/api/v1/projects/{project_id}/responses/{target['id']}",
            json={"response_status": "responded", "response_note": "部分响应，见技术方案第三章"},
            headers=headers,
        )
        assert rescoped.status_code == 200, rescoped.text
        # 前端逐条按钮只发状态+说明，不能顺手把已关联的章节与证据清掉
        assert rescoped.json()["section_ref"] == "技术方案-3", rescoped.json()
        assert rescoped.json()["evidence_refs"] == ["产品白皮书 v3"], rescoped.json()
        assert rescoped.json()["response_note"] == "部分响应，见技术方案第三章"

        linked = await client.patch(
            f"/api/v1/projects/{project_id}/responses/{target['id']}",
            json={
                "response_note": "部分响应，见技术方案第三章",
                "section_ref": "技术方案-4",
                "evidence_refs": ["产品白皮书 v3", "CMMI3 证书"],
            },
            headers=headers,
        )
        assert linked.status_code == 200, linked.text
        assert linked.json()["response_status"] == "responded"
        assert linked.json()["section_ref"] == "技术方案-4", linked.json()
        assert linked.json()["evidence_refs"] == ["产品白皮书 v3", "CMMI3 证书"]
        # 只补章节/证据不能把确认时间刷成"刚刚"，否则责任时点失真
        assert linked.json()["confirmed_at"] == rescoped.json()["confirmed_at"], linked.json()

        listed = await client.get(f"/api/v1/projects/{project_id}/responses", headers=headers)
        summary = listed.json()["summary"]
        assert summary["responded"] == 1
        assert summary["pending"] == body["total"] - 1
        assert listed.json()["unresolved"] == body["total"] - 1

        rebuilt = await client.post(
            f"/api/v1/projects/{project_id}/responses/build", headers=headers
        )
        assert rebuilt.status_code == 200, rebuilt.text
        assert rebuilt.json()["summary"]["responded"] == 1, "rebuild must preserve confirmations"

        invalid = await client.patch(
            f"/api/v1/projects/{project_id}/responses/{target['id']}",
            json={"response_status": "unknown"},
            headers=headers,
        )
        assert invalid.status_code == 422
    finally:
        await client.aclose()


TENDER_V2 = """
某市政务云运维服务项目招标文件（第二版）
项目预算：人民币 180 万元。
投标人须具备有效的营业执照及 CMMI3 级及以上资质。
投标人须提供近三年经审计的财务报告。
所投产品须提供原厂授权书。
系统应支持双活架构，全年可用性不低于 99.99%。
服务期内须提供 7x24 小时响应，故障响应时间不超过 30 分钟。
评分采用综合评分法：
1. 技术方案：45 分
2. 投标报价：55 分
未按招标文件要求密封的投标将被否决。
"""


async def test_confirmed_responses_survive_new_constraint_version() -> None:
    client, headers, project_id = await _setup("responses-version")
    try:
        first = await client.post(
            f"/api/v1/projects/{project_id}/responses/build", headers=headers
        )
        assert first.status_code == 200, first.text
        target = next(
            item
            for item in first.json()["items"]
            if "CMMI3" in item["requirement_text"]
        )
        patched = await client.patch(
            f"/api/v1/projects/{project_id}/responses/{target['id']}",
            json={
                "response_status": "responded",
                "response_note": "CMMI3 证书见附件",
                "evidence_refs": ["CMMI3 证书"],
                "section_ref": "资格文件-1",
            },
            headers=headers,
        )
        assert patched.status_code == 200, patched.text
        confirmed_at = patched.json()["confirmed_at"]

        second_upload = await client.post(
            f"/api/v1/projects/{project_id}/documents",
            files={"file": ("tender-v2.txt", TENDER_V2.encode("utf-8"), "text/plain")},
            headers=headers,
        )
        assert second_upload.status_code == 200, second_upload.text
        assert second_upload.json()["constraint"]["version"] == 2

        rebuilt = await client.post(
            f"/api/v1/projects/{project_id}/responses/build", headers=headers
        )
        assert rebuilt.status_code == 200, rebuilt.text
        body = rebuilt.json()
        assert body["constraint_version"] == 2
        carried = next(
            item for item in body["items"] if "CMMI3" in item["requirement_text"]
        )
        assert carried["response_status"] == "responded", carried
        assert carried["response_note"] == "CMMI3 证书见附件", carried
        assert carried["evidence_refs"] == ["CMMI3 证书"], carried
        assert carried["section_ref"] == "资格文件-1", carried
        assert carried["confirmed_at"] == confirmed_at, carried
        assert carried["id"] != target["id"], "新版本应创建自己的响应行"

        changed = next(
            item for item in body["items"] if "99.99" in item["requirement_text"]
        )
        assert changed["response_status"] == "pending", changed

        default_list = await client.get(
            f"/api/v1/projects/{project_id}/responses", headers=headers
        )
        assert default_list.status_code == 200, default_list.text
        assert default_list.json()["constraint_version"] == 2
        default_ids = {item["id"] for item in default_list.json()["items"]}
        assert target["id"] not in default_ids, "默认列表不能混入旧版本响应"
        assert carried["id"] in default_ids

        historical = await client.get(
            f"/api/v1/projects/{project_id}/responses?constraint_version=1",
            headers=headers,
        )
        assert historical.status_code == 200, historical.text
        assert historical.json()["constraint_version"] == 1
        historical_ids = {item["id"] for item in historical.json()["items"]}
        assert target["id"] in historical_ids, "历史版本必须可追溯"
        assert any("99.95" in item["requirement_text"] for item in historical.json()["items"])

        missing = await client.get(
            f"/api/v1/projects/{project_id}/responses?constraint_version=999",
            headers=headers,
        )
        assert missing.status_code == 404, missing.text

        old_patch = await client.patch(
            f"/api/v1/projects/{project_id}/responses/{target['id']}",
            json={"response_status": "responded"},
            headers=headers,
        )
        assert old_patch.status_code == 409, old_patch.text

        old_batch = await client.post(
            f"/api/v1/projects/{project_id}/responses/batch",
            json={"response_ids": [target["id"]], "response_status": "responded"},
            headers=headers,
        )
        assert old_batch.status_code == 409, old_batch.text
    finally:
        await client.aclose()


async def test_response_matrix_is_tenant_scoped() -> None:
    client_a, headers_a, project_a = await _setup("responses-b")
    client_b, headers_b, _ = await _setup("responses-c")
    try:
        built = await client_a.post(
            f"/api/v1/projects/{project_a}/responses/build", headers=headers_a
        )
        response_id = built.json()["items"][0]["id"]

        foreign_list = await client_b.get(
            f"/api/v1/projects/{project_a}/responses", headers=headers_b
        )
        assert foreign_list.status_code == 404

        foreign_patch = await client_b.patch(
            f"/api/v1/projects/{project_a}/responses/{response_id}",
            json={"response_status": "responded"},
            headers=headers_b,
        )
        assert foreign_patch.status_code == 404
    finally:
        await client_a.aclose()
        await client_b.aclose()


CLARIFICATION = """
某市政务云运维服务项目澄清通知
各投标人：现对招标文件作如下澄清：
1. 技术方案评分由 40 分调整为 45 分。
2. 关于资质问题答复：具备乙级即可。
"""


async def test_clarification_merges_with_tender_and_enters_response_matrix() -> None:
    client, headers, project_id = await _setup("responses-clarify")
    try:
        clarification = await client.post(
            f"/api/v1/projects/{project_id}/documents",
            files={"file": ("clarify.txt", CLARIFICATION.encode("utf-8"), "text/plain")},
            headers=headers,
        )
        assert clarification.status_code == 200, clarification.text
        payload = clarification.json()["constraint"]["payload"]
        assert payload["clarifications"], payload
        assert payload["parse_meta"].get("merged_clarification") is True
        # 澄清文件不得覆盖原招标文件已解析出的要求
        assert payload["scoring_items"], payload["scoring_items"]
        assert payload["qualification_requirements"], payload["qualification_requirements"]
        # 澄清文件不得用自己的误解析结果顶掉原招标的评分表
        scores = [str(item.get("item")) for item in payload["scoring_items"]]
        assert any("技术方案" in text for text in scores), scores
        assert not any(text.strip() == "调整为" for text in scores), scores

        built = await client.post(
            f"/api/v1/projects/{project_id}/responses/build", headers=headers
        )
        items = built.json()["items"]
        types = {item["requirement_type"] for item in items}
        assert "clarification" in types, types
        assert {"qualification", "scoring"} <= types, types
    finally:
        await client.aclose()


async def test_response_matrix_exports_xlsx() -> None:
    client, headers, project_id = await _setup("responses-export")
    try:
        await client.post(f"/api/v1/projects/{project_id}/responses/build", headers=headers)
        exported = await client.get(
            f"/api/v1/projects/{project_id}/responses/export", headers=headers
        )
        assert exported.status_code == 200, exported.text
        assert exported.content.startswith(b"PK"), "xlsx must be a zip container"
        assert "spreadsheetml" in exported.headers["content-type"]
        assert "attachment" in exported.headers["content-disposition"]

        workbook = load_workbook(BytesIO(exported.content))
        sheet = workbook["Responses"]
        headers = [cell.value for cell in sheet[1]]
        assert headers[:6] == ["序号", "类型", "类别", "招标要求原文", "招标出处", "响应状态"], headers
        sources = [sheet.cell(row=index, column=5).value for index in range(2, sheet.max_row + 1)]
        assert all(value for value in sources), "每条响应都要能翻回招标文件里的出处"
        assert any("须具备" in str(value) for value in sources), sources
    finally:
        await client.aclose()


async def test_export_blocked_until_responses_resolved() -> None:
    client, headers, project_id = await _setup("responses-gate")
    try:
        await client.post(f"/api/v1/projects/{project_id}/responses/build", headers=headers)
        blocked = await client.post(
            f"/api/v1/projects/{project_id}/export/docx",
            json={"dark_bid": False},
            headers=headers,
        )
        assert blocked.status_code == 409, blocked.text
        assert "未处理" in blocked.text

        acknowledged = await client.post(
            f"/api/v1/projects/{project_id}/export/docx",
            json={"dark_bid": False, "acknowledge_unresolved": True},
            headers=headers,
        )
        # 强制导出放行闸门后，才会走到“没有章节”的业务校验
        assert acknowledged.status_code != 409, acknowledged.text
    finally:
        await client.aclose()


async def test_batch_marks_responses_and_clears_gate() -> None:
    client, headers, project_id = await _setup("responses-batch")
    try:
        built = await client.post(f"/api/v1/projects/{project_id}/responses/build", headers=headers)
        ids = [item["id"] for item in built.json()["items"]]
        assert len(ids) >= 4

        empty = await client.post(
            f"/api/v1/projects/{project_id}/responses/batch",
            json={"response_ids": [], "response_status": "responded"},
            headers=headers,
        )
        assert empty.status_code == 422, empty.text

        rejected = await client.post(
            f"/api/v1/projects/{project_id}/responses/batch",
            json={"response_ids": [*ids, str(uuid4())], "response_status": "responded"},
            headers=headers,
        )
        assert rejected.status_code == 404, rejected.text
        partial = await client.get(f"/api/v1/projects/{project_id}/responses", headers=headers)
        assert partial.json()["summary"]["responded"] == 0, "batch must not half-apply"

        batch = await client.post(
            f"/api/v1/projects/{project_id}/responses/batch",
            json={
                "response_ids": ids,
                "response_status": "responded",
                "response_note": "统一响应，详见各章节",
            },
            headers=headers,
        )
        assert batch.status_code == 200, batch.text
        assert batch.json()["updated"] == len(ids)
        assert batch.json()["unresolved"] == 0

        listed = await client.get(f"/api/v1/projects/{project_id}/responses", headers=headers)
        for item in listed.json()["items"]:
            assert item["response_note"] == "统一响应，详见各章节"
            assert item["confirmed_by"], "batch confirmation must record who confirmed"

        exported = await client.post(
            f"/api/v1/projects/{project_id}/export/docx",
            json={"dark_bid": False},
            headers=headers,
        )
        assert exported.status_code != 409, exported.text
    finally:
        await client.aclose()


async def test_export_blocked_before_matrix_is_built() -> None:
    client, headers, project_id = await _setup("responses-unbuilt")
    try:
        # 跳过"生成响应表"这一步过去等于无声绕过闸门，现在同样必须被拦
        blocked = await client.post(
            f"/api/v1/projects/{project_id}/export/docx",
            json={"dark_bid": False},
            headers=headers,
        )
        assert blocked.status_code == 409, blocked.text
        assert "尚未生成逐条响应表" in blocked.text, blocked.text

        acknowledged = await client.post(
            f"/api/v1/projects/{project_id}/export/docx",
            json={"dark_bid": False, "acknowledge_unresolved": True},
            headers=headers,
        )
        assert acknowledged.status_code != 409, acknowledged.text

        built = await client.post(
            f"/api/v1/projects/{project_id}/responses/build", headers=headers
        )
        ids = [item["id"] for item in built.json()["items"]]
        assert ids, built.text
        await client.post(
            f"/api/v1/projects/{project_id}/responses/batch",
            headers=headers,
            json={"response_ids": ids, "response_status": "responded"},
        )
        cleared = await client.post(
            f"/api/v1/projects/{project_id}/export/docx",
            json={"dark_bid": False},
            headers=headers,
        )
        assert cleared.status_code != 409, cleared.text
    finally:
        await client.aclose()
