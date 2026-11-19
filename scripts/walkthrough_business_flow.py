"""按企业投标部门的真实业务动线走查（对已部署实例发真实 HTTP，无 mock）。

动线：登录 → 企业资产 → 商机 → 建项目 → 解析招标文件 → 决策
     → 模板草稿 → 大纲 → 生成 → 合规 → 就绪/自检
     → 交标回执 + 附件 → 中标/合同 → 导出（品牌）→ 计量 → 开放 API → 审计

用法：
    BIDCOPILOT_DEMO_PASSWORD='<demo-password>' python scripts/walkthrough_business_flow.py

口令从 BIDCOPILOT_DEMO_PASSWORD 读取，无内置默认值；缺失时启动即报错退出。
"""

import os
import sys
from uuid import uuid4

import httpx

BASE = "http://127.0.0.1:18080"
TENDER = """某市政务云运维服务项目招标文件
项目预算：人民币 150 万元。
投标截止时间：2027-01-08 10:00。
投标人须具备信息系统集成资质，且不得指定品牌或限制本地注册。
评标办法：技术方案 60 分，商务报价 40 分。
"""

results: list[tuple[str, str, str]] = []
# 每次运行的唯一标识：让 Webhook / 令牌等「按值去重」的资源不撞历史数据，
# 从而保证脚本可重复执行（幂等）。
RUN_ID = uuid4().hex[:8]
created: dict[str, list[str]] = {"webhooks": [], "tokens": []}


def check(stage: str, name: str, ok: bool, detail: str = "") -> None:
    results.append(("PASS" if ok else "FAIL", f"{stage} {name}", detail))


c = httpx.Client(base_url=BASE, timeout=180)

# ---------- 0. 登录 ----------
PASSWORD = os.environ.get("BIDCOPILOT_DEMO_PASSWORD")
if not PASSWORD:
    print("请设置 BIDCOPILOT_DEMO_PASSWORD（演示账号口令，脚本不内置默认值）", file=sys.stderr)
    sys.exit(2)
r = c.post("/api/v1/auth/login", json={"email": "demo@example.com", "password": PASSWORD})
check("S0", "登录", r.status_code == 200, str(r.status_code))
token = r.json()["access_token"]
H = {"Authorization": f"Bearer {token}"}
me = c.get("/api/v1/users/me", headers=H)
tenant_id = me.json()["tenant_id"] if me.status_code == 200 else "?"

# ---------- 1. 企业资产（投标主体）----------
r = c.post("/api/v1/companies", json={"name": "走查投标企业科技有限公司", "credit_code": "91330000WZTEST01X"}, headers=H)
ok = r.status_code in (200, 201, 409)
company_id = r.json().get("id") if r.status_code in (200, 201) else None
if company_id is None and ok:
    lst = c.get("/api/v1/companies", headers=H).json()
    company_id = next((x["id"] for x in lst if x["name"] == "走查投标企业科技有限公司"), None)
check("S1", "建投标企业", ok and bool(company_id), f"company_id={company_id}")

# ---------- 2. 商机 ----------
r = c.get("/api/v1/opportunities?limit=5", headers=H)
check("S2", "商机列表", r.status_code == 200, f"{len(r.json().get('items', r.json())) if r.status_code == 200 else r.status_code}")

# ---------- 3. 建项目（关联企业）----------
r = c.post("/api/v1/projects", json={"name": "走查-政务云运维项目", "company_id": company_id}, headers=H)
check("S3", "建项目(带企业)", r.status_code == 201, str(r.status_code))
pid = r.json()["id"]
proj = r.json()
check("S3b", "项目企业归属", proj.get("company_id") == company_id, f"company_id={proj.get('company_id')}")
check("S3c", "项目无 client_id 字段", "client_id" not in proj, "已下线")

# ---------- 4. 上传并解析招标文件 ----------
r = c.post(
    f"/api/v1/projects/{pid}/documents",
    files={"file": ("tender.txt", TENDER.encode(), "text/plain")},
    headers=H,
)
check("S4", "上传招标文件", r.status_code in (200, 201), str(r.status_code))

r = c.get(f"/api/v1/projects/{pid}/constraints", headers=H)
ok = r.status_code == 200
payload = r.json().get("payload", {}) if ok else {}
budget = payload.get("budget") or {}
check(
    "S4b",
    "约束抽取",
    ok and bool(budget.get("amount")),
    f"budget={budget.get('amount')} {budget.get('currency')} confidence={budget.get('confidence')}",
)

# ---------- 5. 决策（投不投）+ 证据链 ----------
r = c.post(f"/api/v1/projects/{pid}/decision-card", json={"inputs": {}}, headers=H)
check("S5", "决策卡计算", r.status_code == 200, str(r.status_code))
if r.status_code == 200:
    d = r.json()
    check("S5b", "决策结论", bool(d.get("status")), f"status={d.get('status')} score={d.get('score')}")
    dims = {x["key"] for x in d.get("dimensions", [])}
    check("S5c", "六维含竞争情报", "competition" in dims, f"dims={sorted(dims)}")

r = c.get(f"/api/v1/projects/{pid}/decision-evidence", headers=H)
ok = r.status_code == 200
ev = r.json() if ok else {}
check("S5d", "决策证据链", ok, f"buyer_match_mode={ev.get('buyer_match_mode')} items={len(ev.get('items', []))}")

# ---------- 6. 指标项 + 大纲 ----------
r = c.get(f"/api/v1/projects/{pid}/scoring-items", headers=H)
check("S6", "评分项", r.status_code == 200, f"{len(r.json()) if r.status_code == 200 else r.status_code} 条")

r = c.post(f"/api/v1/projects/{pid}/outline", json={}, headers=H)
ok = r.status_code == 200
sections = r.json() if ok else []
check("S6b", "大纲生成", ok and len(sections) > 0, f"{len(sections)} 章节")

# ---------- 7. 知识库 → 模板草稿自动生成 ----------
r = c.post("/api/v1/knowledge/bases", json={"name": "走查知识库", "scope": "personal"}, headers=H)
kb_id = r.json().get("id") if r.status_code in (200, 201) else None
check("S7", "建个人知识库", bool(kb_id), str(r.status_code))

if kb_id:
    for i in range(3):
        doc = (
            f"投标文件样例{i + 1}\n"
            "第一章 投标函\n我方愿意参加本项目投标，并承担相应责任。\n"
            "第二章 技术方案\n采用容器化架构，提供 7x24 运维值守与实施计划。\n"
            "第三章 商务报价\n报价包含全部费用，附报价表。\n"
            "第四章 售后服务\n提供 1 年质保与培训方案。\n"
        )
        c.post(
            "/api/v1/knowledge/documents",
            files={"file": (f"case{i + 1}.txt", doc.encode(), "text/plain")},
            data={"kb_id": kb_id, "kind": "case", "tags": "历史标书"},
            headers=H,
        )
    r = c.post(f"/api/v1/knowledge/bases/{kb_id}/template-draft", json={}, headers=H)
    ok = r.status_code in (200, 201)
    body = r.json() if ok else {}
    check(
        "S7b",
        "模板草稿自动生成",
        ok and body.get("status") == "draft",
        f"status={body.get('status')} sections={len(body.get('content_json', {}).get('sections', []))}",
    )
    check("S7c", "草稿无 client_id", "client_id" not in body, str(r.status_code))

# ---------- 8. 合规扫描 ----------
r = c.post(
    f"/api/v1/projects/{pid}/compliance/scan",
    files={"file": ("tender.txt", TENDER.encode(), "text/plain")},
    headers=H,
)
check("S8", "合规扫描", r.status_code in (200, 201), str(r.status_code))

# ---------- 9. 就绪清单 + 自检 ----------
r = c.get(f"/api/v1/projects/{pid}/readiness", headers=H)
check("S9", "交标就绪清单", r.status_code == 200, f"items={len(r.json().get('items', [])) if r.status_code == 200 else r.status_code}")

r = c.get(f"/api/v1/projects/{pid}/self-check", headers=H)
check("S9b", "投标自检", r.status_code == 200, str(r.status_code))

# ---------- 10. 跨投标人雷同自查（交标前终检）----------
files = [
    ("files", ("a.txt", ("我方技术方案：采用容器化架构，提供 7x24 运维值守。" * 20).encode(), "text/plain")),
    ("files", ("b.txt", ("本公司技术方案：使用容器化架构，提供 7x24 小时运维值守。" * 20).encode(), "text/plain")),
]
r = c.post("/api/v1/file-check/similarity", files=files, headers=H)
ok = r.status_code == 200
sim = r.json() if ok else {}
check(
    "S10",
    "跨投标人雷同自查",
    ok,
    f"pairs={len(sim.get('pairs', []))} status={sim.get('status')}",
)
if ok and sim.get("pairs"):
    p0 = sim["pairs"][0]
    check("S10b", "语义分与依据链", "semantic_score" in p0 and "semantic_evidence" in p0, f"sem={p0.get('semantic_score')}")

# ---------- 11. 交标回执 + 附件留痕 ----------
r = c.post(
    f"/api/v1/projects/{pid}/submission-receipts",
    json={"submitted_at": "2027-01-08T10:00:00+08:00", "platform": "浙江政府采购网", "receipt_no": "WZ-2027-001"},
    headers=H,
)
ok = r.status_code in (200, 201)
receipt_id = r.json().get("id") if ok else None
check("S11", "交标回执", ok, f"receipt={receipt_id}")

if receipt_id:
    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000a49444154789c6300010000050001a5f645400000000049454e44ae426082"
    )
    r = c.post(
        f"/api/v1/projects/{pid}/submission-receipts/{receipt_id}/attachments",
        files={"file": ("回执.png", png, "image/png")},
        data={"kind": "receipt"},
        headers=H,
    )
    ok = r.status_code in (200, 201)
    att = r.json() if ok else {}
    check("S11b", "回执附件上传", ok, f"sha256={str(att.get('sha256'))[:12]} size={att.get('size_bytes')}")
    if ok:
        r2 = c.get(
            f"/api/v1/projects/{pid}/submission-receipts/{receipt_id}/attachments/{att['id']}",
            headers=H,
        )
        check("S11c", "附件下载", r2.status_code == 200 and len(r2.content) == att.get("size_bytes"), f"{len(r2.content)}B")

# ---------- 12. 交付摘要（品牌 = 投标企业自身）----------
r = c.get(f"/api/v1/projects/{pid}/delivery-summary", headers=H)
ok = r.status_code == 200
ds = r.json() if ok else {}
brand = ds.get("brand", {})
check("S12", "交付摘要", ok, f"brand={brand.get('name')}")
check(
    "S12b",
    "品牌为企业自身且无 client 键",
    brand.get("name") == "走查投标企业科技有限公司" and "client" not in ds,
    f"brand={brand.get('name')} client_key={'client' in ds}",
)
check("S12c", "回执附件汇总", "receipt_attachments" in ds, f"{ds.get('receipt_attachments')}")

# ---------- 13. 导出（xlsx 就绪清单 + docx 标书）----------
r = c.get(f"/api/v1/projects/{pid}/readiness/export", headers=H)
check("S13", "就绪清单 xlsx 导出", r.status_code == 200, f"{len(r.content)}B")

r = c.get(f"/api/v1/projects/{pid}/responses/export", headers=H)
check("S13b", "逐条响应 xlsx 导出", r.status_code in (200, 409), str(r.status_code))

# ---------- 14. AI 用量（按项目计量）----------
r = c.get(f"/api/v1/projects/{pid}/ai-usage", headers=H)
ok = r.status_code == 200
usage = r.json() if ok else {}
check("S14", "项目 AI 用量", ok, f"pieces={usage.get('pieces')} words={usage.get('words')}")

# ---------- 15. 开放 API：令牌 + Webhook ----------
r = c.post("/api/v1/api-tokens", json={"name": f"走查令牌-{RUN_ID}", "scopes": ["project:read"]}, headers=H)
ok = r.status_code in (200, 201)
tk_body = r.json() if ok else {}
if ok and tk_body.get("id"):
    created["tokens"].append(tk_body["id"])
check("S15", "创建 API 令牌", ok and tk_body.get("token", "").startswith("btc_"), f"prefix={tk_body.get('token_prefix')}")
if ok:
    plain = tk_body["token"]
    r = c.get("/api/v1/projects", headers={"Authorization": f"Bearer {plain}"})
    check("S15b", "令牌可鉴权", r.status_code == 200, str(r.status_code))
    lst = c.get("/api/v1/api-tokens", headers=H).json()
    leaked = any(str(v).startswith("btc_") and len(str(v)) > 20 for row in lst for v in row.values() if v)
    check("S15c", "列表不泄露明文", not leaked, "仅 prefix")

r = c.post("/api/v1/webhooks", json={"url": f"https://example.com/hook-{RUN_ID}", "events": ["project.status.changed"]}, headers=H)
ok = r.status_code in (200, 201)
wh = r.json() if ok else {}
if ok and wh.get("id"):
    created["webhooks"].append(wh["id"])
check("S15d", "创建 Webhook", ok, f"id={wh.get('id')}")
r = c.post("/api/v1/webhooks", json={"url": "https://127.0.0.1/hook", "events": ["project.status.changed"]}, headers=H)
check("S15e", "Webhook SSRF 拒绝", r.status_code == 422, str(r.status_code))

# ---------- 16. 审计 ----------
r = c.get("/api/v1/audit/events?limit=5", headers=H)
check("S16", "审计可查", r.status_code == 200, f"{len(r.json()) if r.status_code == 200 else r.status_code} 条")

# ---------- 收尾清理（保持环境可重复走查）----------
for wid in created["webhooks"]:
    c.delete(f"/api/v1/webhooks/{wid}", headers=H)
for tid in created["tokens"]:
    c.delete(f"/api/v1/api-tokens/{tid}", headers=H)
c.delete(f"/api/v1/projects/{pid}", headers=H)
c.delete(f"/api/v1/companies/{company_id}", headers=H)

# ---------- 输出 ----------
print(f"{'结果':6s} {'环节':30s} 说明")
print("-" * 90)
for status, name, detail in results:
    print(f"{status:6s} {name:32s} {detail}")
failed = [x for x in results if x[0] == "FAIL"]
print()
print(f"结果：{len(results) - len(failed)} PASS / {len(failed)} FAIL")
print(f"走查项目 id（可删）：{pid}")
sys.exit(1 if failed else 0)
