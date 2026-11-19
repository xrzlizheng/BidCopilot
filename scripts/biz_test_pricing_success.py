"""BidCopilot 智能报价策略实测（真实 HTTP + 真实 PostgreSQL）。

用法：
    BIDCOPILOT_DEMO_PASSWORD='<demo-password>' python scripts/biz_test_pricing_success.py

口令从 BIDCOPILOT_DEMO_PASSWORD 读取，无内置默认值；缺失时启动即报错退出。
"""
import asyncio, json, os, sys, uuid
from datetime import UTC, datetime
from decimal import Decimal as D
import httpx
from sqlalchemy import delete, select

# 口令从 BIDCOPILOT_DEMO_PASSWORD 读取，无内置默认值；缺失时启动即报错退出。
# 守卫放在 app 导入之前：app.config 的 Settings 校验会在导入期先炸出无关报错。
PASSWORD = os.environ.get("BIDCOPILOT_DEMO_PASSWORD")
if not PASSWORD:
    print("请设置 BIDCOPILOT_DEMO_PASSWORD（演示账号口令，脚本不内置默认值）", file=sys.stderr)
    raise SystemExit(2)

from app import main as _m  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.modules.identity.models import Tenant  # noqa: E402
from app.modules.opportunities.models import Opportunity  # noqa: E402
from app.modules.projects.models import BidProject, BidResult  # noqa: E402
from app.modules.system.models import TenantSetting  # noqa: E402

TARGET_PROJECT = "某市政务云系统集成项目"
INDUSTRY = "系统集成测试"

async def main():
    opp_ids, proj_ids = [], []
    async with SessionLocal() as s:
        tid = (await s.execute(select(Tenant).where(Tenant.name=="演示投标企业"))).scalars().first().id
        s.add(TenantSetting(tenant_id=tid, key="pricing_cost_model", value={
            "direct_rate":"0.55","indirect_rate":"0.12","tax_rate":"0.06",
            "labor_rates":{"项目经理":"1500","开发工程师":"1200"}}))
        budget = D("5200000")
        for i, ratio in enumerate(["0.80","0.85","0.88","0.90","0.95","0.92"]):
            o = Opportunity(tenant_id=tid, source="test", dedup_key=uuid.uuid4().hex, title=f"[TEST] 中标 {i}",
                            industry=[INDUSTRY], budget_amount=budget, win_bid_amount=budget*D(ratio),
                            info_type="award", fetched_at=datetime.now(UTC))
            s.add(o); await s.flush()
            p = BidProject(tenant_id=tid, name=f"[TEST] 中标 {i}", status="won", opportunity_id=o.id)
            s.add(p); await s.flush()
            s.add(BidResult(tenant_id=tid, project_id=p.id, outcome="won", win_amount=budget*D(ratio)))
            opp_ids.append(o.id); proj_ids.append(p.id)
        for i in range(3):
            o = Opportunity(tenant_id=tid, source="test", dedup_key=uuid.uuid4().hex, title=f"[TEST] 失标 {i}",
                            industry=[INDUSTRY], budget_amount=budget, info_type="award", fetched_at=datetime.now(UTC))
            s.add(o); await s.flush()
            p = BidProject(tenant_id=tid, name=f"[TEST] 失标 {i}", status="lost", opportunity_id=o.id)
            s.add(p); await s.flush()
            s.add(BidResult(tenant_id=tid, project_id=p.id, outcome="lost", loss_reason="price"))
            opp_ids.append(o.id); proj_ids.append(p.id)
        await s.commit()

    rc = 1
    try:
        c = httpx.Client(base_url="http://127.0.0.1:18080", timeout=180)
        tok = c.post("/api/v1/auth/login", json={"email":"demo@example.com","password":PASSWORD}).json()["access_token"]
        H = {"Authorization": f"Bearer {tok}"}
        pid = [p for p in c.get("/api/v1/projects", headers=H).json() if p["name"]==TARGET_PROJECT][0]["id"]
        print("target project:", TARGET_PROJECT, pid)
        body = {"inputs": {"quote_amount":"4800000.00","budget_amount":"5200000.00","other_direct_cost":"180000.00",
                "labor_items":[{"role":"项目经理","days":"120"},{"role":"开发工程师","days":"360"}],
                "industry":INDUSTRY, "include_narrative":True}}
        st = c.post(f"/api/v1/projects/{pid}/pricing/strategy", headers=H, json=body).json()
        print("POST status:", st.get("status"))
        latest = c.get(f"/api/v1/projects/{pid}/pricing/strategy/latest", headers=H).json()
        print("latest status:", latest.get("status"), "| id:", latest.get("id"))
        for t in latest.get("tiers") or []:
            print("  tier:", json.dumps({k:t.get(k) for k in ("tier","price","target_margin","net_margin","win_rate","band_position","status")}, ensure_ascii=False))
        print("  band:", json.dumps({k:(latest.get("price_band") or {}).get(k) for k in ("status","sample_count","p25","p50","p75","p25_amount","p50_amount","p75_amount")}, ensure_ascii=False))
        print("  sens rows:", len((latest.get("sensitivity") or {}).get("rows") or []))
        print("  narrative.source:", (latest.get("narrative") or {}).get("source"))
        rc = 0 if latest.get("status")=="ok" and len(latest.get("tiers") or [])==3 else 1
        print("RESULT:", "PASS" if rc==0 else "FAIL")
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(BidResult).where(BidResult.project_id.in_(proj_ids)))
            await s.execute(delete(BidProject).where(BidProject.id.in_(proj_ids)))
            await s.execute(delete(Opportunity).where(Opportunity.id.in_(opp_ids)))
            await s.execute(delete(TenantSetting).where(TenantSetting.tenant_id==tid, TenantSetting.key=="pricing_cost_model"))
            await s.commit()
        await engine.dispose()
        print("history cleanup done (strategy snapshot kept)")
    return rc

raise SystemExit(asyncio.run(main()))
