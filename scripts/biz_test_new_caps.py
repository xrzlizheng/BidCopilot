"""BidCopilot 新能力实际业务测试 v2（真实 HTTP + 真实 PostgreSQL）。

用法：
    BIDCOPILOT_DEMO_PASSWORD='<demo-password>' python scripts/biz_test_new_caps.py

口令从 BIDCOPILOT_DEMO_PASSWORD 读取，无内置默认值；缺失时启动即报错退出。
"""
import json, os, sys, uuid
from datetime import date, timedelta
import httpx

BASE = "http://127.0.0.1:18080"
results = []
def rec(ok, name, detail=""):
    results.append((ok, name, detail))
    print(f"{'PASS' if ok else 'FAIL'} | {name}" + (f" | {detail}" if detail else ""))

def main():
    password = os.environ.get("BIDCOPILOT_DEMO_PASSWORD")
    if not password:
        print("请设置 BIDCOPILOT_DEMO_PASSWORD（演示账号口令，脚本不内置默认值）", file=sys.stderr)
        return 2
    c = httpx.Client(base_url=BASE, timeout=120)
    r = c.post("/api/v1/auth/login", json={"email":"demo@example.com","password":password})
    assert r.status_code == 200, r.text
    H = {"Authorization": f"Bearer {r.json()['access_token']}"}
    me = c.get("/api/v1/auth/me", headers=H).json()
    rec(True, "登录", f"tenant={me.get('tenant_name')}")
    projects = c.get("/api/v1/projects", headers=H).json()
    pid = projects[0]["id"]
    rec(True, "取项目", f"id={pid}")

    print("\n===== A. 智能报价策略 =====")
    r = c.get(f"/api/v1/projects/{pid}/pricing/price-band", headers=H)
    pb = r.json() if r.status_code == 200 else {}
    band = pb.get("price_band") or {}
    rec(r.status_code == 200, "价格带 GET (原 500 已修复)", f"HTTP {r.status_code} outer={pb.get('status')} band={band.get('status')} samples={band.get('sample_count')}")
    print("   P25/P50/P75(率):", band.get("p25"), band.get("p50"), band.get("p75"))
    print("   P25/P50/P75(额):", band.get("p25_amount"), band.get("p50_amount"), band.get("p75_amount"))
    print("   样本来源:", band.get("samples_by_source"), "| missing:", band.get("missing"))

    body = {"inputs": {
        "quote_amount":"4800000.00","budget_amount":"5200000.00","other_direct_cost":"180000.00",
        "labor_items":[{"role":"项目经理","days":"120"},{"role":"开发工程师","days":"360"}],
        "include_narrative": True}}
    r = c.post(f"/api/v1/projects/{pid}/pricing/strategy", headers=H, json=body)
    st = r.json() if r.status_code == 200 else {}
    rec(r.status_code == 200, "生成报价策略 POST", f"HTTP {r.status_code} status={st.get('status')}")
    if r.status_code == 200:
        print("   顶层字段:", sorted(st.keys()))
        print("   cost:", json.dumps(st.get("cost"), ensure_ascii=False)[:220])
        print("   price_band.status:", (st.get("price_band") or {}).get("status"))
        print("   tiers:", len(st.get("tiers") or []))
        for t in (st.get("tiers") or []):
            print("     档位:", t.get("tier"), "建议价:", t.get("quote_amount"), "净利率:", t.get("net_margin"))
        print("   sensitivity.rows:", len((st.get("sensitivity") or {}).get("rows") or []))
        print("   narrative.source:", (st.get("narrative") or {}).get("source"))
        print("   missing:", st.get("missing"))
        rec(bool(st.get("tiers")), "三档建议价", f"tiers={len(st.get('tiers') or [])}")
        rec(len((st.get("sensitivity") or {}).get("rows") or []) == 7, "敏感性 7 行", f"rows={len((st.get('sensitivity') or {}).get('rows') or [])}")
        rec("非" in str(st.get("disclaimer")) or "启发式" in str(st.get("disclaimer")), "诚实免责声明", str(st.get("disclaimer"))[:60])

    rec(c.get(f"/api/v1/projects/{pid}/pricing/strategy/latest", headers=H).status_code==200, "策略持久化 GET latest")

    print("\n===== B. 自动匹配投标机会 =====")
    s0 = c.get("/api/v1/opportunities/auto-match/settings", headers=H).json()
    rec(True, "匹配设置 GET", json.dumps(s0, ensure_ascii=False)[:150])
    run = c.post("/api/v1/opportunities/auto-match/run", headers=H, json={"limit":30}).json()
    rec(run.get("errors",1)==0, "批量自动匹配", json.dumps(run, ensure_ascii=False)[:200])
    items = (c.get("/api/v1/opportunities", headers=H, params={"page_size":5}).json().get("items") or [])
    oid = items[0]["id"]
    m = c.post(f"/api/v1/opportunities/{oid}/match", headers=H)
    mj = m.json() if m.status_code==200 else {}
    rec(m.status_code==200, "单条匹配重算", f"score={mj.get('match_score')} decision={mj.get('decision')} qualified={mj.get('qualified')} gaps={len(mj.get('qualification_gaps') or [])}")
    print("   reasons:", mj.get("reasons"))
    print("   risks:", mj.get("risks"))
    print("   hit_groups:", mj.get("hit_groups"))
    rec(c.get(f"/api/v1/opportunities/{oid}/match", headers=H).status_code==200, "匹配结果持久化 GET")

    print("\n===== C. 合同管理 CLM =====")
    rb = {"project_id":pid,"contract_no":f"HT-BIZ-{uuid.uuid4().hex[:6]}","title":"业务测试合同2",
          "party_a":"某市政务云管理中心","party_b":"演示投标企业",
          "signed_date":str(date.today()),"effective_date":str(date.today()),
          "expiry_date":str(date.today()+timedelta(days=30)),"amount":"4800000.00","currency":"CNY"}
    r = c.post("/api/v1/contract-records", headers=H, json=rb)
    recd = r.json() if r.status_code in (200,201) else {}
    rid = recd.get("id")
    rec(r.status_code in (200,201), "创建合同主数据", f"status={recd.get('status')}")
    if rid:
        dr = c.post(f"/api/v1/contract-records/{rid}/draft", headers=H, json={}).json()
        clauses = (dr.get("record") or {}).get("draft") or []
        rec(len(clauses)>0, "起草合同草稿", f"clauses={len(clauses)} 来源字段={bool(clauses and clauses[0].get('source_json'))}")
        print("   免责:", str(dr.get("disclaimer"))[:70])
        rec(c.post(f"/api/v1/contract-records/{rid}/submit", headers=H, json={"comment":"提交"}).json().get("status")=="pending_approval", "提交审批")
        rec(c.post(f"/api/v1/contract-records/{rid}/approve", headers=H, json={"comment":"同意"}).json().get("status")=="active", "审批通过→active")
        for et, amt in [("invoice","1600000.00"),("invoice","800000.00"),("payment","1200000.00")]:
            assert c.post(f"/api/v1/contract-records/{rid}/ledger", headers=H,
                   json={"entry_type":et,"amount":amt,"occurred_on":str(date.today())}).status_code in (200,201)
        s = c.get(f"/api/v1/contract-records/{rid}/ledger", headers=H).json()["summary"]
        rec(s["invoice_total"]=="2400000.00" and s["outstanding"]=="1200000.00", "台账汇总与 Decimal 精度", json.dumps(s, ensure_ascii=False))
        ch = c.post(f"/api/v1/contract-records/{rid}/changes", headers=H,
                    json={"change_type":"renewal","expiry_before":str(date.today()+timedelta(days=30)),
                          "expiry_after":str(date.today()+timedelta(days=395)),"reason":"续签一年"})
        rec(ch.status_code in (200,201), "续签登记", f"HTTP {ch.status_code}")
        x = c.get(f"/api/v1/contract-records/{rid}/export", headers=H)
        rec(x.status_code==200 and len(x.content)>2000, "台账 xlsx 导出", f"bytes={len(x.content)}")
        rec(c.post(f"/api/v1/contract-records/{rid}/submit", headers=H, json={"comment":"x"}).status_code==409, "非法状态跳转→409")
        rec(c.get(f"/api/v1/contract-records/{rid}/approvals", headers=H).status_code==200, "审批留痕查询")

    print("\n===== D. 隔离与权限 =====")
    bogus = str(uuid.uuid4())
    rec(c.get(f"/api/v1/contract-records/{bogus}", headers=H).status_code==404, "不存在合同→404")
    rec(c.get(f"/api/v1/projects/{bogus}/pricing/price-band", headers=H).status_code==404, "不存在项目报价→404")
    rec(c.get(f"/api/v1/opportunities/{bogus}/match", headers=H).status_code==404, "不存在商机匹配→404")
    rec(c.get("/api/v1/contract-records", headers=H, params={"status":"active"}).status_code==200, "合同列表状态筛选")

    print("\n===== 汇总 =====")
    p = sum(1 for ok,_,_ in results if ok); f = len(results)-p
    print(f"TOTAL={len(results)} PASS={p} FAIL={f}")
    if f:
        print("失败项:", [n for ok,n,_ in results if not ok])
    return 0 if f==0 else 1

sys.exit(main())
