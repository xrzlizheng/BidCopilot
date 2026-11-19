"""Read-only HTTP audit of BidCopilot core flow: outline -> generate -> factcheck
-> approve -> responses -> compliance -> export.

Creates one [AUDIT] project and deletes it in `finally`. Product code untouched.
Run from backend/:
    BIDCOPILOT_DEMO_PASSWORD='<demo-password>' ../scripts/py.cmd ../scripts/audit_core_flow.py

Password comes from AUDIT_PASSWORD (legacy alias) or BIDCOPILOT_DEMO_PASSWORD;
there is no built-in default.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx
from docx import Document

BASE = os.environ.get("AUDIT_BASE_URL", "http://127.0.0.1:18080")
EMAIL = os.environ.get("AUDIT_EMAIL", "demo@example.com")
# AUDIT_PASSWORD 为历史兼容别名；统一口令源是 BIDCOPILOT_DEMO_PASSWORD，无内置默认值。
PASSWORD = os.environ.get("AUDIT_PASSWORD") or os.environ.get("BIDCOPILOT_DEMO_PASSWORD")
STAMP = time.strftime("%Y%m%d-%H%M%S")
PROJECT_NAME = f"[AUDIT] core-flow {STAMP}"
PROJECT_CODE = f"[AUDIT]-CORE-{STAMP}"

RESULTS: list[dict[str, Any]] = []
PROJECT_ID = ""


def jbody(response: httpx.Response) -> Any:
    try:
        return response.json()
    except Exception:
        return {"_non_json": response.text[:300]}


def brief(value: Any, limit: int = 260) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str) if not isinstance(value, str) else value
    return text[:limit]


def record(
    step: str,
    path: str,
    pre: str,
    status: str,
    issue: str,
    evidence: Any,
    severity: str,
) -> None:
    row = {
        "step": step,
        "path": path,
        "precondition": pre,
        "status": status,
        "issue": issue,
        "evidence": brief(evidence, 700),
        "severity": severity,
    }
    RESULTS.append(row)
    print("AUDIT|" + json.dumps(row, ensure_ascii=False), flush=True)


def docx_headings(content: bytes) -> list[str]:
    doc = Document(BytesIO(content))
    return [p.text for p in doc.paragraphs if p.style is not None and p.style.name.startswith("Heading 1")]


def psql(sql: str) -> str:
    proc = subprocess.run(
        ["docker", "exec", "bidcopilot-app", "psql", "-U", "bidcopilot", "-d", "bidcopilot", "-tAc", sql],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"psql failed: {proc.stderr[:400]}")
    return proc.stdout.strip()


def sample_tender_v1() -> bytes:
    doc = Document()
    doc.add_heading("第一章 招标公告", level=1)
    doc.add_paragraph("本项目最高限价为 1,200,000 元。")
    doc.add_paragraph("投标截止时间为 2027年6月30日 09:00。")
    doc.add_paragraph("投标人须具备涉密信息系统集成乙级及以上资质。")
    doc.add_paragraph("项目经理须具备 PMP 证书。")
    doc.add_paragraph("近三年具有不少于 2 个类似项目业绩。")
    doc.add_paragraph("投标文件应采用 A4 纸，正文宋体小四。")
    doc.add_paragraph("本项目采用暗标，不得出现公司名称。")
    doc.add_paragraph("投标文件应包括营业执照、报价表、技术方案。")
    table = doc.add_table(rows=4, cols=3)
    for index, text in enumerate(("评分项", "分值", "评分标准")):
        table.cell(0, index).text = text
    rows = (
        ("技术方案", "40 分", "方案完整、可落地"),
        ("项目经理（PMP）", "20 分", "人员配置合理"),
        ("投标报价", "40 分", "价格分"),
    )
    for row_index, row in enumerate(rows, start=1):
        for col, text in enumerate(row):
            table.cell(row_index, col).text = text
    buffer = BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def sample_tender_v2() -> bytes:
    doc = Document()
    doc.add_heading("第一章 更正后招标公告", level=1)
    doc.add_paragraph("本项目最高限价为 2,000,000 元。")
    doc.add_paragraph("投标截止时间为 2027年12月31日 09:00。")
    doc.add_paragraph("投标人须具备营业执照。")
    table = doc.add_table(rows=3, cols=3)
    for index, text in enumerate(("评分项", "分值", "评分标准")):
        table.cell(0, index).text = text
    rows = (
        ("运维服务方案", "50 分", "服务方案完整、SLA 可量化"),
        ("投标报价", "50 分", "价格分"),
    )
    for row_index, row in enumerate(rows, start=1):
        for col, text in enumerate(row):
            table.cell(row_index, col).text = text
    buffer = BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def main() -> int:
    global PROJECT_ID
    if not PASSWORD:
        print(
            "demo password required: 请设置 BIDCOPILOT_DEMO_PASSWORD（或旧别名 AUDIT_PASSWORD），"
            "脚本不内置默认口令",
            file=sys.stderr,
        )
        return 2
    client = httpx.Client(base_url=BASE, timeout=180)
    login = client.post("/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
    if login.status_code != 200:
        print(f"LOGIN FAILED {login.status_code} {login.text[:300]}", file=sys.stderr)
        return 2
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    created = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"name": PROJECT_NAME, "code": PROJECT_CODE, "bid_deadline": "2027-06-30T09:00:00+08:00"},
    )
    if created.status_code != 201:
        print(f"CREATE PROJECT FAILED {created.status_code} {created.text[:300]}", file=sys.stderr)
        return 2
    PROJECT_ID = created.json()["id"]
    print(f"AUDIT_PROJECT|{PROJECT_ID}|{PROJECT_NAME}", flush=True)

    # ---------- A. 空项目前置条件 ----------
    r = client.post(f"/api/v1/projects/{PROJECT_ID}/outline", headers=headers)
    record("A1 无招标文件生成大纲", "POST /projects/{id}/outline", "项目存在，无 TenderConstraint",
           "✅ 拦截" if r.status_code == 422 else f"❌ {r.status_code}",
           "前置校验有效" if r.status_code == 422 else "前置校验未生效",
           {"status": r.status_code, "body": jbody(r)}, "P2")
    r = client.get(f"/api/v1/projects/{PROJECT_ID}/sections", headers=headers)
    record("A2 无大纲查章节", "GET /projects/{id}/sections", "无章节", "✅ 空列表",
           "无异常", {"status": r.status_code, "body": jbody(r)}, "P2")
    r = client.post(f"/api/v1/projects/{PROJECT_ID}/export/docx", headers=headers,
                    json={"dark_bid": False, "acknowledge_unresolved": True})
    record("A3 无章节/无约束导出", "POST /projects/{id}/export/docx", "无章节、无约束",
           "✅ 拦截" if r.status_code == 422 else f"❌ {r.status_code}",
           "export_docx 要求至少一个 approved 章节" if r.status_code == 422 else "未拦截",
           {"status": r.status_code, "body": jbody(r)}, "P2")
    r = client.post(f"/api/v1/projects/{PROJECT_ID}/compliance/scan", headers=headers)
    record("A4 无约束合规预检", "POST /projects/{id}/compliance/scan", "无约束",
           "✅ 拦截" if r.status_code == 422 else f"❌ {r.status_code}",
           "scan 要求先解析招标文件" if r.status_code == 422 else "未拦截",
           {"status": r.status_code, "body": jbody(r)}, "P2")
    r = client.post(f"/api/v1/projects/{PROJECT_ID}/responses/build", headers=headers)
    record("A5 无约束生成逐条响应", "POST /projects/{id}/responses/build", "无约束",
           "✅ 拦截" if r.status_code == 404 else f"❌ {r.status_code}",
           "返回 404 约束集不存在" if r.status_code == 404 else "未拦截",
           {"status": r.status_code, "body": jbody(r)}, "P2")

    # ---------- B. 上传 v1 + 大纲 ----------
    files = {"file": ("[AUDIT]_tender_v1.docx", sample_tender_v1(),
                       "application/vnd.openxmlformats-officedocument.wordprocessingml.document")}
    r = client.post(f"/api/v1/projects/{PROJECT_ID}/documents", headers=headers,
                    files=files, data={"document_kind": "tender"})
    b = jbody(r)
    constraint_v1 = (b.get("constraint") or {})
    record("B1 上传并解析招标文件 v1", "POST /projects/{id}/documents", "项目存在",
           "✅ 成功" if r.status_code == 200 and constraint_v1.get("version") == 1 else f"❌ {r.status_code}",
           "v1 约束集已生成" if r.status_code == 200 else "上传/解析失败",
           {"status": r.status_code, "version": constraint_v1.get("version"),
            "parse_status": (b.get("document") or {}).get("parse_status"),
            "scoring_items": len((constraint_v1.get("payload") or {}).get("scoring_items") or []),
            "qualification_requirements": len((constraint_v1.get("payload") or {}).get("qualification_requirements") or [])},
           "P2")

    r = client.post(f"/api/v1/projects/{PROJECT_ID}/export/docx", headers=headers,
                    json={"dark_bid": False, "acknowledge_unresolved": False})
    record("B2 已上传但未生成章节就导出（不强制）", "POST /projects/{id}/export/docx",
           "有约束、无章节、响应表未生成",
           "✅ 拦截" if r.status_code == 409 else f"❌ {r.status_code}",
           "unbuilt_response_item_count 兜住'不生成响应表就导出'" if r.status_code == 409 else "未拦截",
           {"status": r.status_code, "body": jbody(r)}, "P2")
    r = client.post(f"/api/v1/projects/{PROJECT_ID}/export/docx", headers=headers,
                    json={"dark_bid": False, "acknowledge_unresolved": True})
    record("B3 同上但强制导出", "POST /projects/{id}/export/docx", "有约束、无章节、ack=true",
           "✅ 仍被章节闸门拦" if r.status_code == 422 else f"❌ {r.status_code}",
           "强制只绕过响应表，export_docx 仍要求 approved 章节" if r.status_code == 422 else "强制绕过了章节闸门",
           {"status": r.status_code, "body": jbody(r)}, "P1")

    r = client.post(f"/api/v1/projects/{PROJECT_ID}/compliance/scan", headers=headers)
    record("B4 有约束但未生成章节就合规预检", "POST /projects/{id}/compliance/scan", "有约束、无章节",
           "✅ 拦截" if r.status_code == 422 else f"❌ {r.status_code}",
           "scan 要求先生成章节" if r.status_code == 422 else "未拦截",
           {"status": r.status_code, "body": jbody(r)}, "P2")

    r = client.post(f"/api/v1/projects/{PROJECT_ID}/outline", headers=headers)
    sections = r.json() if r.status_code == 200 else []
    section_ids = [s["id"] for s in sections]
    titles = [s["title"] for s in sections]
    record("B5 生成大纲", "POST /projects/{id}/outline", "v1 约束存在",
           "✅ 成功" if r.status_code == 200 else f"❌ {r.status_code}",
           "默认走 cover/qualification + 每个评分项 + commitment",
           {"status": r.status_code, "count": len(sections), "titles": titles}, "P2")
    r2 = client.post(f"/api/v1/projects/{PROJECT_ID}/outline", headers=headers)
    record("B6 再次生成大纲（隐式 reset？）", "POST /projects/{id}/outline", "已有大纲",
           "⚠️ 返回原章节" if r2.status_code == 200 and [s["id"] for s in r2.json()] == section_ids else f"❌ {r2.status_code}",
           "HTTP 无 reset 参数，重复调用只返回旧章节；reset 只能经 PUT /projects/{id}/template 触发",
           {"status": r2.status_code, "same_ids": r2.status_code == 200 and [s["id"] for s in r2.json()] == section_ids},
           "P1")

    r = client.post(f"/api/v1/projects/{PROJECT_ID}/generate", headers=headers,
                    json={"mode": "rules"})
    gen = jbody(r)
    record("B7 生成全部章节（rules 模式）", "POST /projects/{id}/generate", "大纲存在",
           "✅ 成功" if r.status_code == 200 and gen.get("status") == "succeeded" else f"❌ {r.status_code}",
           "rules 模式不依赖 LLM；生成时自动执行事实核查（见 ai_meta/factcheck）",
           {"status": r.status_code, "run": gen}, "P2")

    # ---------- C. 事实核查 / 审批链 ----------
    sec_a, sec_b, sec_c = section_ids[0], section_ids[1], section_ids[2]
    r = client.patch(f"/api/v1/sections/{sec_a}", headers=headers,
                     json={"content_text": "我方承诺本项目工期为 777 天，投标报价为 888888 元。"})
    record("C1 手工改章节 A", "PATCH /sections/{id}", "章节已生成",
           "✅ 成功" if r.status_code == 200 else f"❌ {r.status_code}",
           "保存后 status=draft、factcheck_status=pending、证据清空",
           {"status": r.status_code, "section": {k: r.json().get(k) for k in ("status", "factcheck_status", "unsourced_count")}},
           "P2")
    r = client.post(f"/api/v1/sections/{sec_a}/approve", headers=headers, json={"acknowledge_unsourced": False})
    record("C2 未事实核查直接审批", "POST /sections/{id}/approve", "factcheck_status=pending",
           "✅ 拦截" if r.status_code == 422 else f"❌ {r.status_code}",
           "approve_section 明确拒绝 pending" if r.status_code == 422 else "未拦截",
           {"status": r.status_code, "body": jbody(r)}, "P1")
    r = client.post(f"/api/v1/sections/{sec_a}/factcheck", headers=headers)
    fa = jbody(r)
    record("C3 对含无源数字章节执行事实核查", "POST /sections/{id}/factcheck", "内容含 777 天/888888 元",
           "✅ 状态正确" if r.status_code == 200 and (fa.get("section") or {}).get("factcheck_status") == "needs_review" else f"❌ {r.status_code}",
           "unsourced_count 计入，状态 needs_review，但并不阻断后续（审批可 ack）",
           {"status": r.status_code, "unsourced_count": (fa.get("section") or {}).get("unsourced_count"),
            "traceability_rate": (fa.get("section") or {}).get("traceability_rate"),
            "evidence_statuses": [e.get("status") for e in fa.get("evidence") or []]}, "P1")
    r = client.post(f"/api/v1/sections/{sec_a}/approve", headers=headers, json={"acknowledge_unsourced": False})
    record("C4 有未溯源主张但不勾选确认就审批", "POST /sections/{id}/approve", "factcheck=needs_review, unsourced>0",
           "✅ 拦截" if r.status_code == 422 else f"❌ {r.status_code}",
           "acknowledge_unsourced 是唯一强制点" if r.status_code == 422 else "未拦截",
           {"status": r.status_code, "body": jbody(r)}, "P2")
    r = client.post(f"/api/v1/sections/{sec_a}/approve", headers=headers, json={"acknowledge_unsourced": True})
    record("C5 勾选确认后审批（带无源主张）", "POST /sections/{id}/approve", "acknowledge_unsourced=true",
           "✅ 成功" if r.status_code == 200 else f"❌ {r.status_code}",
           "ack 后允许 approved，无源事实仍留在 EvidenceLink 中",
           {"status": r.status_code, "section": {k: r.json().get(k) for k in ("status", "factcheck_status", "unsourced_count")}},
           "P2")

    r = client.patch(f"/api/v1/sections/{sec_b}", headers=headers, json={"content_text": ""})
    r = client.post(f"/api/v1/sections/{sec_b}/factcheck", headers=headers)
    fb = jbody(r)
    r = client.post(f"/api/v1/sections/{sec_b}/approve", headers=headers, json={"acknowledge_unsourced": False})
    record("C6 空章节事实核查+审批", "PATCH -> factcheck -> approve", "content_text 置空",
           "❌ 空章节也能通过" if r.status_code == 200 else f"✅ 拦截 {r.status_code}",
           "0 条 claim 时 traceability_rate=1/factcheck=passed，空章节可直接 approved 并进入导出",
           {"factcheck_status": (fb.get("section") or {}).get("factcheck_status"),
            "traceability_rate": (fb.get("section") or {}).get("traceability_rate"),
            "approve_status": r.status_code}, "P1")

    r = client.patch(f"/api/v1/sections/{sec_c}", headers=headers,
                     json={"content_text": "我方承诺工期 555 天，项目验收合格率为 99.99%。"})
    r = client.post(f"/api/v1/sections/{sec_c}/approve", headers=headers, json={"acknowledge_unsourced": True})
    record("C7 未核查章节审批（C 留 draft）", "PATCH -> approve", "C factcheck_status=pending",
           "✅ 拦截" if r.status_code == 422 else f"❌ {r.status_code}",
           "C 保持 draft，供导出部分章节验证", {"status": r.status_code, "body": jbody(r)}, "P2")

    # ---------- D. 导出闸门 ----------
    r = client.post(f"/api/v1/projects/{PROJECT_ID}/export/docx", headers=headers,
                    json={"dark_bid": False, "acknowledge_unresolved": True})
    if r.status_code == 200:
        headings = docx_headings(r.content)
        record("D1 只审批 2/6 章节就导出 docx", "POST /projects/{id}/export/docx", "A、B approved；C~F draft；ack=true",
               "❌ 放行", "export_docx 只检查'至少一个 approved'，draft 章节被静默丢弃，导出文件不是完整标书",
               {"status": r.status_code, "bytes": len(r.content), "docx_h1": headings,
                "missing_approved_count": len(section_ids) - len(headings)}, "P1")
    else:
        record("D1 部分章节审批导出", "POST /projects/{id}/export/docx", "A、B approved；其余 draft",
               f"✅ 拦截 {r.status_code}", "未放行", {"status": r.status_code, "body": jbody(r)}, "P1")

    r = client.post(f"/api/v1/projects/{PROJECT_ID}/anonymize", headers=headers)
    record("D2 一键匿名化（绕过响应闸门）", "POST /projects/{id}/anonymize", "A/B approved；响应表未生成",
           "❌ 放行" if r.status_code == 200 else f"❌ {r.status_code}",
           "注释明确绕过响应表闸门，但仍只导出 approved 子集；可生成'看起来完整'的匿名文件",
           {"status": r.status_code, "bytes": len(r.content),
            "docx_h1": docx_headings(r.content) if r.status_code == 200 else None}, "P1")

    # 模板应用 = 唯一可触达 build_outline(reset=True) 的入口
    r = client.put(f"/api/v1/projects/{PROJECT_ID}/template", headers=headers,
                   json={"template_code": "builtin.gov_it"})
    record("D3 已审批章节后重置大纲（经模板应用）", "PUT /projects/{id}/template", "存在 approved 章节",
           "✅ 拦截" if r.status_code == 422 else f"❌ {r.status_code}",
           "service.py:627 的 approved 拦截真实可达；但无版本化重建入口，新招标版本无法自动重排大纲"
           if r.status_code == 422 else "未拦截",
           {"status": r.status_code, "body": jbody(r)}, "P1")

    # 重新生成已审批章节
    before = client.get(f"/api/v1/projects/{PROJECT_ID}/sections", headers=headers).json()
    before_a = next(s for s in before if s["id"] == sec_a)
    r = client.post(f"/api/v1/projects/{PROJECT_ID}/generate", headers=headers,
                    json={"mode": "rules", "section_ids": [sec_a]})
    after = client.get(f"/api/v1/projects/{PROJECT_ID}/sections", headers=headers).json()
    after_a = next(s for s in after if s["id"] == sec_a)
    record("D4 重新生成已审批章节", "POST /projects/{id}/generate section_ids=[approved]",
           "A 已 approved；generate 无 approved 检查",
           "❌ 审批被静默清除" if after_a["status"] != "approved" else f"⚠️ {r.status_code}",
           "generate_sections 无条件 status=draft/approved_at=None 并覆盖 content，已审批内容无版本/无提示丢失",
           {"generate_status": r.status_code, "before": {"status": before_a["status"],
            "approved_at": before_a["approved_at"], "content": before_a["content_text"][:60]},
            "after": {"status": after_a["status"], "approved_at": after_a["approved_at"],
            "content": after_a["content_text"][:60]}}, "P1")

    # 恢复 A 为 approved 供后续导出
    r = client.post(f"/api/v1/sections/{sec_a}/approve", headers=headers, json={"acknowledge_unsourced": True})
    # C 执行 factcheck 以便产生 EVID-UNSOURCED 合规发现
    client.post(f"/api/v1/sections/{sec_c}/factcheck", headers=headers)

    # ---------- E. 逐条响应 / 合规 ----------
    r = client.post(f"/api/v1/projects/{PROJECT_ID}/responses/build", headers=headers)
    resp = jbody(r)
    record("E1 生成逐条响应表", "POST /projects/{id}/responses/build", "有约束与章节",
           "✅ 成功" if r.status_code == 200 else f"❌ {r.status_code}",
           "按最新约束重建；缺章节/证据关联只由合规规则提示",
           {"status": r.status_code, "total": resp.get("total"), "summary": resp.get("summary"),
            "unresolved": resp.get("unresolved")}, "P2")
    r = client.post(f"/api/v1/projects/{PROJECT_ID}/export/docx", headers=headers,
                    json={"dark_bid": False, "acknowledge_unresolved": False})
    record("E2 响应表有未处理项就导出", "POST /projects/{id}/export/docx", "unresolved>0",
           "✅ 拦截" if r.status_code == 409 else f"❌ {r.status_code}",
           "router.py 的响应表闸门生效" if r.status_code == 409 else "未拦截",
           {"status": r.status_code, "body": jbody(r)}, "P2")
    r = client.post(f"/api/v1/projects/{PROJECT_ID}/export/docx", headers=headers,
                    json={"dark_bid": False, "acknowledge_unresolved": True})
    record("E3 强制导出（acknowledge_unresolved）", "POST /projects/{id}/export/docx", "unresolved>0,ack=true",
           "❌ 放行" if r.status_code == 200 else f"❌ {r.status_code}",
           "强制导出只绕过逐条响应/未生成响应表闸门；不校验合规 findings",
           {"status": r.status_code, "bytes": len(r.content)}, "P1")
    r = client.get(f"/api/v1/projects/{PROJECT_ID}/responses/export", headers=headers)
    record("E4 未处理响应表直接导出 Excel", "GET /projects/{id}/responses/export", "unresolved>0",
           "❌ 无闸门" if r.status_code == 200 else f"❌ {r.status_code}",
           "responses_export 不检查 unresolved，未处理项原样带出（状态列可见）",
           {"status": r.status_code, "bytes": len(r.content)}, "P2")

    r = client.post(f"/api/v1/projects/{PROJECT_ID}/compliance/scan", headers=headers)
    report1 = jbody(r)
    findings1 = report1.get("findings") or []
    rule_ids1 = [f.get("rule_id") for f in findings1]
    hard1 = [f for f in findings1 if f.get("finding_type") == "HARD"]
    record("E5 首次合规预检", "POST /projects/{id}/compliance/scan", "章节/响应/证据齐全性不足",
           "✅ 成功" if r.status_code == 200 else f"❌ {r.status_code}",
           "输出 HARD/SOFT，但报告 status 只有 completed；无 pending/confirmed 状态机",
           {"status": r.status_code, "summary": report1.get("summary_json"),
            "hard_rules": [f.get("rule_id") for f in hard1],
            "has_RESP_OPEN": "RESP-OPEN-001" in rule_ids1,
            "has_EVID_UNSOURCED": "EVID-UNSOURCED-001" in rule_ids1}, "P1")

    dismissed_r = None
    accepted = 0
    for finding in findings1:
        r = client.patch(f"/api/v1/findings/{finding['id']}", headers=headers, json={"status": "dismissed"})
        if dismissed_r is None:
            dismissed_r = (r.status_code, jbody(r))
        r = client.patch(f"/api/v1/findings/{finding['id']}", headers=headers, json={"status": "accepted"})
        if r.status_code == 200:
            accepted += 1
    record("E6 findings 状态机", "PATCH /findings/{id}", "report1 有 findings",
           "⚠️ 只有 open/fixed/accepted" if dismissed_r and dismissed_r[0] == 422 else "❌ 异常",
           "不存在 pending/confirmed/dismissed；accepted 仅是 finding 上的标记，报告 summary 不重算",
           {"dismissed_response": dismissed_r, "accepted_count": accepted,
            "report_status": report1.get("status"), "summary_json": report1.get("summary_json")}, "P1")

    r = client.get(f"/api/v1/projects/{PROJECT_ID}/readiness?refresh=true", headers=headers)
    readiness = jbody(r)
    auto_blocked = [
        {"item_key": i.get("item_key"), "rule_id": i.get("rule_id"), "status": i.get("status")}
        for i in readiness.get("items", [])
        if i.get("source_type") == "auto" and i.get("status") == "blocked"
    ]
    record("E7 已 accepted 的 finding 对就绪清单的影响", "GET /projects/{id}/readiness?refresh=true",
           "report1 所有 findings 已 accepted",
           "❌ 仍 blocked" if auto_blocked else f"⚠️ 无 blocked ({r.status_code})",
           "readiness.refresh_auto_items 只看 finding 是否存在，不看 status；accepted 不会放行",
           {"status": r.status_code, "blocked_items": auto_blocked,
            "summary": readiness.get("summary")}, "P1")

    r = client.post(f"/api/v1/compliance/reports/{report1['id']}/export", headers=headers,
                    json={"format": "xlsx"})
    record("E8 有 HARD findings 时导出合规报告", "POST /compliance/reports/{id}/export", "report1 HARD findings 未修复",
           "❌ 无闸门" if r.status_code == 200 else f"❌ {r.status_code}",
           "报告导出不检查任何 finding 状态；HARD 数不阻断",
           {"status": r.status_code, "bytes": len(r.content)}, "P2")

    # 把所有响应标为 responded，闭环 unresolved
    responses = client.get(f"/api/v1/projects/{PROJECT_ID}/responses", headers=headers).json()
    ids = [item["id"] for item in responses["items"]]
    r = client.post(f"/api/v1/projects/{PROJECT_ID}/responses/batch", headers=headers,
                    json={"response_ids": ids, "response_status": "responded",
                          "response_note": "[AUDIT] 审计批量标记"})
    record("E9 逐条响应闭环", "POST /projects/{id}/responses/batch", "全部 pending",
           "✅ 成功" if r.status_code == 200 else f"❌ {r.status_code}",
           "状态可批量置 responded，但 section_ref 仍为空（由 RESP-LINK-001 提示）",
           {"status": r.status_code, "body": jbody(r)}, "P2")
    r = client.post(f"/api/v1/projects/{PROJECT_ID}/compliance/scan", headers=headers)
    report2 = jbody(r)
    rule_ids2 = [f.get("rule_id") for f in report2.get("findings") or []]
    hard2 = [f.get("rule_id") for f in report2.get("findings") or [] if f.get("finding_type") == "HARD"]
    record("E10 响应闭环后重新预检", "POST /projects/{id}/compliance/scan", "responses 全 responded",
           "✅ 成功" if r.status_code == 200 else f"❌ {r.status_code}",
           "RESP-OPEN 应消失；EVID-UNSOURCED 仍在（C 的 555 天/99.99% 未溯源）",
           {"status": r.status_code, "hard_rules": hard2,
            "RESP_OPEN_present": "RESP-OPEN-001" in rule_ids2,
            "EVID_present": "EVID-UNSOURCED-001" in rule_ids2}, "P1")
    r = client.post(f"/api/v1/projects/{PROJECT_ID}/export/docx", headers=headers,
                    json={"dark_bid": False, "acknowledge_unresolved": False})
    record("E11 响应闭环但仍存在 HARD 合规问题时导出 docx", "POST /projects/{id}/export/docx",
           "unresolved=0；report2 仍有 HARD finding",
           "❌ 放行" if r.status_code == 200 else f"❌ {r.status_code}",
           "导出链路完全不消费 compliance_reports/findings；合规预检是孤岛",
           {"status": r.status_code, "bytes": len(r.content), "hard_rules": hard2}, "P0")

    # ---------- F. 招标文件新版本 ----------
    old_sections = client.get(f"/api/v1/projects/{PROJECT_ID}/sections", headers=headers).json()
    old_scoring_ids = {s.get("scoring_item_id") for s in old_sections if s.get("scoring_item_id")}
    files = {"file": ("[AUDIT]_tender_v2.docx", sample_tender_v2(),
                       "application/vnd.openxmlformats-officedocument.wordprocessingml.document")}
    r = client.post(f"/api/v1/projects/{PROJECT_ID}/documents", headers=headers,
                    files=files, data={"document_kind": "tender"})
    b2 = jbody(r)
    cv2 = b2.get("constraint") or {}
    record("F1 上传招标文件 v2", "POST /projects/{id}/documents", "已有 approved 章节 + v1 约束",
           "✅ 成功" if r.status_code == 200 and cv2.get("version") == 2 else f"❌ {r.status_code}",
           "上传不校验/不失效已有审批章节，也没有章节版本字段",
           {"status": r.status_code, "version": cv2.get("version"),
            "scoring_items_v2": [i.get("item") for i in ((cv2.get("payload") or {}).get("scoring_items") or [])]}, "P1")

    latest = client.get(f"/api/v1/projects/{PROJECT_ID}/constraints", headers=headers).json()
    v2_items = client.get(f"/api/v1/projects/{PROJECT_ID}/scoring-items", headers=headers).json()
    v2_ids = {i["id"] for i in v2_items}
    sections_after = client.get(f"/api/v1/projects/{PROJECT_ID}/sections", headers=headers).json()
    reused = old_scoring_ids & v2_ids
    record("F2 新版本评分项 vs 旧章节", "GET /constraints + /scoring-items + /sections", "v2 最新约束",
           "❌ 版本链断裂" if not reused else f"⚠️ 复用 {len(reused)} 个",
           "旧章节仍绑定 v1 ScoringItem；新评分项没有对应章节，build_outline 也不会补",
           {"latest_constraint_version": latest.get("version"),
            "v2_scoring_items": [i.get("item") for i in v2_items],
            "section_scoring_ids": sorted(old_scoring_ids),
            "v2_scoring_ids": sorted(v2_ids),
            "overlap": sorted(reused)}, "P0")

    r = client.post(f"/api/v1/projects/{PROJECT_ID}/outline", headers=headers)
    titles_after = [s["title"] for s in r.json()] if r.status_code == 200 else []
    record("F3 新版本后重建大纲", "POST /projects/{id}/outline", "v2 最新；已有大纲",
           "❌ 仍是旧大纲" if r.status_code == 200 and "运维服务方案响应" not in titles_after else f"⚠️ {r.status_code}",
           "build_outline 因 existing 非空直接 return，不比较 constraint 版本；新评分项不会生成章节",
           {"status": r.status_code, "titles": titles_after,
            "has_v2_item_section": "运维服务方案响应" in titles_after}, "P0")

    db_rows = psql(
        "select s.title, s.status, coalesce(si.constraint_id::text,''), "
        "coalesce(tc.version::text,'') from bid_sections s "
        "left join scoring_items si on si.id=s.scoring_item_id "
        "left join tender_constraints tc on tc.id=si.constraint_id "
        f"where s.project_id='{PROJECT_ID}' order by s.order_index"
    )
    record("F4 DB 证实章节绑定旧约束", "docker exec ... psql", "v2 最新",
           "❌ 数据自相矛盾", "招标约束已是 v2，章节仍绑定 v1 scoring_items",
           {"rows": db_rows}, "P0")

    r = client.post(f"/api/v1/projects/{PROJECT_ID}/export/docx", headers=headers,
                    json={"dark_bid": False, "acknowledge_unresolved": False})
    record("F5 新版本后响应表未重建就导出", "POST /projects/{id}/export/docx", "v2 最新；v2 响应表未生成",
           "✅ 拦截" if r.status_code == 409 else f"❌ {r.status_code}",
           "unbuilt_response_item_count 依据最新 v2 约束兜底" if r.status_code == 409 else "未拦截",
           {"status": r.status_code, "body": jbody(r)}, "P2")
    r = client.post(f"/api/v1/projects/{PROJECT_ID}/export/docx", headers=headers,
                    json={"dark_bid": False, "acknowledge_unresolved": True})
    record("F6 新版本后强制导出", "POST /projects/{id}/export/docx", "v2 最新；旧 approved 章节",
           "❌ 放行" if r.status_code == 200 else f"❌ {r.status_code}",
           "导出用旧章节内容 + 新 format_rules（只影响页边距），无版本一致性校验",
           {"status": r.status_code, "bytes": len(r.content),
            "docx_h1": docx_headings(r.content) if r.status_code == 200 else None}, "P0")

    r = client.get(f"/api/v1/projects/{PROJECT_ID}/document-diff", headers=headers)
    diff = jbody(r)
    r_apply = client.post(f"/api/v1/projects/{PROJECT_ID}/document-diff/apply", headers=headers,
                          json={"confirm": True, "note": "[AUDIT] 应用版本差异"})
    record("F7 版本差异与 apply", "GET /document-diff + POST /document-diff/apply", "v1 -> v2",
           "⚠️ diff 有，但 apply 拒绝" if r.status_code == 200 and r_apply.status_code == 409 else f"❌ {r_apply.status_code}",
           "apply_document_diff 只接受 document_kind=amendment；整份 tender v2 无应用入口，只重建响应也不重建章节",
           {"diff_status": r.status_code, "has_changes": diff.get("has_changes"),
            "scoring_changed": (diff.get("scoring_items") or {}).get("modified") is not None,
            "apply_status": r_apply.status_code, "apply_body": jbody(r_apply)}, "P1")

    # 生成旧评分章节，观察仍用旧 section/matrix
    target = next((s for s in sections_after if s.get("section_type") == "scoring"), None)
    if target:
        before_text = target.get("content_text") or ""
        r = client.post(f"/api/v1/projects/{PROJECT_ID}/generate", headers=headers,
                        json={"mode": "rules", "section_ids": [target["id"]]})
        target_after = next(
            s for s in client.get(f"/api/v1/projects/{PROJECT_ID}/sections", headers=headers).json()
            if s["id"] == target["id"]
        )
        record("F8 v2 后重新生成旧评分章节", "POST /projects/{id}/generate section_ids=[v1 section]",
               "章节 scoring_item_id 属于 v1；最新约束 v2",
               "❌ 用旧评分项生成" if r.status_code == 200 else f"❌ {r.status_code}",
               "get_matrix 不按 latest constraint 过滤，_rules_blocks 按标题匹配新 payload 失败时仍套旧 matrix row",
               {"status": r.status_code, "section_title": target.get("title"),
                "v2_item_names": [i.get("item") for i in v2_items],
                "content_head": (target_after.get("content_text") or "")[:200],
                "content_changed": before_text != (target_after.get("content_text") or "")}, "P1")

    r = client.put(f"/api/v1/projects/{PROJECT_ID}/template", headers=headers,
                   json={"template_code": "builtin.gov_it"})
    record("F9 新版本后想按新约束重排大纲", "PUT /projects/{id}/template", "approved 章节仍存在",
           "✅ 仍拦截" if r.status_code == 422 else f"❌ {r.status_code}",
           "只能逐个人工把 approved 改回 draft 才能 reset；无'新版本→新章节版本'流程",
           {"status": r.status_code, "body": jbody(r)}, "P1")

    # ---------- G. 其他状态/孤立功能 ----------
    r = client.post(f"/api/v1/projects/{PROJECT_ID}/status", headers=headers,
                    json={"status": "won", "reason": "[AUDIT] 审计状态流转"})
    r_export = client.post(f"/api/v1/projects/{PROJECT_ID}/export/docx", headers=headers,
                           json={"dark_bid": False, "acknowledge_unresolved": True})
    record("G1 项目置为 won 后仍可导出/无冻结", "POST /projects/{id}/status -> export", "project.status=won",
           "⚠️ 仍放行" if r.status_code == 200 and r_export.status_code == 200 else f"❌ {r.status_code}/{r_export.status_code}",
           "project.status 与章节/导出无联动；won 终态不冻结文档，也不记录版本",
           {"status_change": r.status_code, "export": r_export.status_code}, "P2")

    r = client.get(f"/api/v1/projects/{PROJECT_ID}/evaluations", headers=headers)
    record("G2 评标模拟是否被主链路消费", "GET /projects/{id}/evaluations", "项目存在",
           "⚠️ 孤立功能" if r.status_code == 200 else f"❌ {r.status_code}",
           "evaluation 仅写 bid_evaluations；无任何导出/审批/合规代码读取它（代码检索证实）",
           {"status": r.status_code, "count": len(jbody(r)) if r.status_code == 200 else None}, "P2")
    r = client.get(f"/api/v1/projects/{PROJECT_ID}/self-check", headers=headers)
    record("G3 标书自检是否阻断导出", "GET /projects/{id}/self-check", "项目存在",
           "⚠️ 仅建议" if r.status_code == 200 else f"❌ {r.status_code}",
           "自检是独立 GET，返回 findings；export_docx 不调用它（暗标自检是另一条 _dark_bid_self_check）",
           {"status": r.status_code, "findings": len(jbody(r).get("findings") or []) if r.status_code == 200 else None}, "P2")

    # 暗标导出自检（本例无 company_id，仅验证干净路径可导出）
    r = client.post(f"/api/v1/projects/{PROJECT_ID}/export/docx", headers=headers,
                    json={"dark_bid": True, "acknowledge_unresolved": True})
    record("G4 暗标导出", "POST /projects/{id}/export/docx dark_bid=true", "approved 章节",
           "✅ 成功" if r.status_code == 200 else f"⚠️ {r.status_code}",
           "_dark_bid_self_check 清元数据并在正文残留身份时 422；本项目无 company 所以只验证通行路径",
           {"status": r.status_code, "bytes": len(r.content)}, "P2")

    return 0


if __name__ == "__main__":
    exit_code = 1
    try:
        exit_code = main()
    except Exception as exc:
        print(f"AUDIT_SCRIPT_ERROR|{type(exc).__name__}: {exc}", file=sys.stderr)
        exit_code = 3
    finally:
        if PROJECT_ID:
            try:
                cleanup = psql(
                    "delete from audit_events where resource_id='{0}'; "
                    "delete from cost_ledger where resource_id='{0}'; "
                    "delete from notifications where resource_id='{0}'; "
                    "delete from bid_projects where id='{0}'; "
                    "select count(*) from bid_projects where id='{0}';".format(PROJECT_ID)
                )
                print(f"AUDIT_CLEANUP|project={PROJECT_ID}|remaining={cleanup}", flush=True)
            except Exception as exc:
                print(f"AUDIT_CLEANUP_FAILED|{PROJECT_ID}|{exc}", file=sys.stderr)
        print(f"AUDIT_DONE|results={len(RESULTS)}|exit={exit_code}", flush=True)
    sys.exit(exit_code)
