"""BidCopilot 商机自动匹配推荐实测（真实 HTTP + 真实 PostgreSQL）。

用法：
    BIDCOPILOT_DEMO_PASSWORD='<demo-password>' python scripts/biz_test_automatch_recommend.py

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

from app import main as _m  # noqa: F401,E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.modules.identity.models import Tenant  # noqa: E402
from app.modules.opportunities.models import Opportunity  # noqa: E402
from app.modules.projects.models import BidProject  # noqa: E402

CONTENT = (
    "第三章 投标人资格要求\n"
    "1. 具有信息系统集成及服务资质\n"
    "2. 具有质量管理体系认证\n"
)


async def main() -> int:
    async with SessionLocal() as s:
        tid = (await s.execute(select(Tenant).where(Tenant.name == "\u6f14\u793a\u6295\u6807\u4f01\u4e1a"))).scalars().first().id
        o = Opportunity(
            tenant_id=tid, source="test", dedup_key=uuid.uuid4().hex,
            title="[TEST] 政务云系统集成（正文含资格要求，无既有项目）",
            industry=["\u7cfb\u7edf\u96c6\u6210\u6d4b\u8bd5"], budget_amount=D("3000000"),
            region="\u5e7f\u4e1c\u7701\u6df1\u5733\u5e02", buyer="buyer-test",
            info_type="tender", content_text=CONTENT, fetched_at=datetime.now(UTC))
        s.add(o)
        await s.commit()
        oid = o.id
    print("seeded opportunity (no project):", oid)

    rc = 1
    c = httpx.Client(base_url="http://127.0.0.1:18080", timeout=120)
    orig = None
    H = {}
    try:
        tok = c.post("/api/v1/auth/login", json={"email": "demo@example.com", "password": PASSWORD}).json()["access_token"]
        H = {"Authorization": "Bearer " + tok}
        orig = c.get("/api/v1/opportunities/auto-match/settings", headers=H).json()
        c.patch("/api/v1/opportunities/auto-match/settings", headers=H, json={
            "enabled": True, "recommend_min_score": 0, "skip_below_score": 0,
            "auto_create_project": True, "notify": True})
        r = c.post("/api/v1/opportunities/" + str(oid) + "/match", headers=H)
        b = r.json() if r.status_code == 200 else {}
        print("match HTTP", r.status_code, "| qualified:", b.get("qualified"),
              "| score:", b.get("match_score"), "| decision:", b.get("decision"))
        async with SessionLocal() as s:
            projs = (await s.execute(select(BidProject).where(BidProject.opportunity_id == oid))).scalars().all()
        print("projects created:", [(str(x.id), x.name[:34], x.status) for x in projs])
        ok = b.get("qualified") is True and b.get("decision") == "recommend" and len(projs) == 1
        print("RESULT:", "PASS" if ok else "FAIL")
        rc = 0 if ok else 1
    finally:
        if orig is not None:
            c.patch("/api/v1/opportunities/auto-match/settings", headers=H, json={
                "enabled": orig["enabled"], "recommend_min_score": orig["recommend_min_score"],
                "skip_below_score": orig["skip_below_score"],
                "auto_create_project": orig["auto_create_project"], "notify": orig["notify"]})
        async with SessionLocal() as s:
            await s.execute(delete(BidProject).where(BidProject.opportunity_id == oid))
            await s.execute(delete(Opportunity).where(Opportunity.id == oid))
            await s.commit()
        await engine.dispose()
        print("cleanup done")
    return rc


raise SystemExit(asyncio.run(main()))
