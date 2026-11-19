"""决策证据链集成测试：真实入库的 award_records 驱动证据；跨租户不可见。

路由注册：Lead 负责在 app/main.py 注册；本测试自带幂等注册，保证单独跑也能过。
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db.session import SessionLocal
from app.main import app
from app.modules.opportunities.models import AwardRecord, Opportunity
from app.modules.projects.decision_evidence_router import router as decision_evidence_router
from tests.support import cleanup_tenants, seed_tenant_user

BUYER = "某市大数据局"
OTHER_BUYER = "另一个城市的财政局"

TENDER = """
某市政府数据中台运维服务项目招标文件
项目预算：人民币 150 万元。
投标截止时间：2027-01-08 10:00。
投标人须具备信息系统集成资质。
"""

# 登录限流按 IP 计数（LOGIN_IP_MAX_ATTEMPTS）。并行跑集成套件时所有进程
# 共用同一个测试客户端 IP，会被别人的失败尝试挤爆成 429。这里给每个测试
# 一个独立的 X-Real-IP（nginx 语义下该头由网关覆盖，测试里由我们自己给），
# 既不改共享配置，也不影响其他 teammate 的用例。
_IP_COUNTER = 0


def _unique_ip() -> str:
    """确定性地给每次登录分配独立 IP，避免并行套件互相挤爆 IP 限流计数。"""
    global _IP_COUNTER
    _IP_COUNTER += 1
    return f"10.99.{_IP_COUNTER // 250}.{_IP_COUNTER % 250 + 1}"


async def _login(client, email: str) -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "Password123!"},
        headers={"X-Real-IP": _unique_ip()},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _register_decision_evidence_router() -> None:
    """幂等注册：Lead 已在 main.py 注册时不重复加。"""
    target = "/api/v1/projects/{project_id}/decision-evidence"
    if any(getattr(route, "path", None) == target for route in app.routes):
        return
    app.include_router(decision_evidence_router)


_register_decision_evidence_router()


async def _seed_awards(
    tenant_id,
    *,
    buyer: str,
    rows: list[tuple[str, str | None, int]],
    source: str = "keeprisk:E_007_1271",
) -> None:
    """入库中标记录：每条 award_records 挂一条 opportunities（采购人名称在商机表上）。"""
    now = datetime.now(UTC)
    async with SessionLocal() as session:
        for index, (winner, amount, days_ago) in enumerate(rows):
            opportunity = Opportunity(
                tenant_id=tenant_id,
                source=source,
                dedup_key=f"{source}-{buyer}-{index}",
                title=f"{buyer} 第 {index + 1} 期中标公告",
                buyer=buyer,
                industry=["政府采购"],
                info_type="award",
                detail_status="list_only",
                confidence="high",
                fetched_at=now,
            )
            session.add(opportunity)
            await session.flush()
            session.add(
                AwardRecord(
                    tenant_id=tenant_id,
                    opportunity_id=opportunity.id,
                    dedup_key=f"award-{source}-{buyer}-{index}",
                    project_name=opportunity.title,
                    winner=winner,
                    win_amount=Decimal(amount) if amount is not None else None,
                    award_at=now - timedelta(days=days_ago),
                    source=source,
                    fetched_at=now,
                )
            )
        await session.commit()


async def _create_project(client, headers, *, name: str, opportunity_id: str | None = None) -> str:
    payload: dict[str, object] = {"name": name}
    if opportunity_id is not None:
        payload["opportunity_id"] = opportunity_id
    response = await client.post("/api/v1/projects", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _link_buyer(project_id, *, tenant_id, buyer: str) -> None:
    """项目 buyer 正常来自关联商机；这里直接落库构造场景，避免依赖同步链路。"""
    from app.modules.projects.models import BidProject

    async with SessionLocal() as session:
        project = (
            await session.execute(
                select(BidProject).where(
                    BidProject.tenant_id == tenant_id, BidProject.id == project_id
                )
            )
        ).scalar_one()
        project.buyer = buyer
        await session.commit()


@pytest.mark.integration
async def test_decision_evidence_driven_by_real_award_records() -> None:
    """真实入库 4 条中标记录 -> 采购人历史/竞争/预算锚点都能给出带依据的结论。"""
    tenant_id, _, email = await seed_tenant_user("decision-evidence")
    try:
        await _seed_awards(
            tenant_id,
            buyer=BUYER,
            rows=[
                ("供应商甲", "1200000", 30),
                ("供应商甲", "1000000", 200),
                ("供应商乙", "900000", 120),
                ("供应商丙", "950000", 400),
            ],
        )
        # 我方在同采购人的历史战果：2 胜 1 负
        async with SessionLocal() as session:
            from app.modules.projects.models import BidProject

            for status in ("won", "won", "lost"):
                session.add(
                    BidProject(
                        tenant_id=tenant_id,
                        name=f"历史项目-{status}",
                        status=status,
                        buyer=BUYER,
                    )
                )
            await session.commit()

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = await _login(client, email)
            project_id = await _create_project(client, headers, name="决策证据项目")
            uploaded = await client.post(
                f"/api/v1/projects/{project_id}/documents",
                files={"file": ("tender.txt", TENDER.encode(), "text/plain")},
                headers=headers,
            )
            assert uploaded.status_code == 200, uploaded.text
            await _link_buyer(project_id, tenant_id=tenant_id, buyer=BUYER)

            response = await client.get(
                f"/api/v1/projects/{project_id}/decision-evidence", headers=headers
            )
            assert response.status_code == 200, response.text
            body = response.json()

        assert body["buyer"] == BUYER
        assert body["buyer_match_mode"] == "exact"
        assert body["window_days"] == 730
        items = {item["key"]: item for item in body["items"]}
        assert set(items) == {"buyer_history", "competition", "our_win_rate", "budget_anchor"}

        history = items["buyer_history"]
        assert history["scored"] is True, history
        assert history["scope"]["record_count"] == 4
        assert history["metrics"]["record_count"] == 4
        # 4 条含金额：900000 / 950000 / 1000000 / 1200000 -> 中位数 (950000+1000000)/2
        assert history["metrics"]["median_win_amount"] == "975000.00"
        assert history["metrics"]["distinct_winners"] == 3
        assert "award_records" in history["sources"]

        competition = items["competition"]
        assert competition["scored"] is True, competition
        assert competition["metrics"]["distinct_winners"] == 3

        win_rate = items["our_win_rate"]
        assert win_rate["scored"] is True, win_rate
        assert win_rate["metrics"]["won"] == 2
        assert win_rate["metrics"]["lost"] == 1

        anchor = items["budget_anchor"]
        assert anchor["scored"] is True, anchor
        assert anchor["metrics"]["project_budget"] == "1500000.00"
        assert anchor["metrics"]["median_win_amount"] == "975000.00"

        assert body["missing"] == []
        assert any("精确匹配" in note for note in body["notes"])
    finally:
        await cleanup_tenants([tenant_id])


@pytest.mark.integration
async def test_decision_evidence_marks_missing_samples_instead_of_fabricating() -> None:
    """没有历史数据时必须显式标 scored=false + reason，不许用 0 冒充统计。"""
    tenant_id, _, email = await seed_tenant_user("decision-evidence-empty")
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = await _login(client, email)
            project_id = await _create_project(client, headers, name="无历史项目")
            uploaded = await client.post(
                f"/api/v1/projects/{project_id}/documents",
                files={"file": ("tender.txt", TENDER.encode(), "text/plain")},
                headers=headers,
            )
            assert uploaded.status_code == 200, uploaded.text
            await _link_buyer(project_id, tenant_id=tenant_id, buyer=BUYER)

            response = await client.get(
                f"/api/v1/projects/{project_id}/decision-evidence", headers=headers
            )
            assert response.status_code == 200, response.text
            body = response.json()

        items = {item["key"]: item for item in body["items"]}
        assert items["buyer_history"]["scored"] is False
        assert "样本不足" in items["buyer_history"]["reason"]
        assert "median_win_amount" not in items["buyer_history"]["metrics"]
        assert items["competition"]["scored"] is False
        assert items["our_win_rate"]["scored"] is False
        assert items["budget_anchor"]["scored"] is False
        # 预算锚点确实没有中标价样本，不能给出 0 元中位数
        assert "median_win_amount" not in items["budget_anchor"]["metrics"]
        assert len(body["missing"]) == 4
    finally:
        await cleanup_tenants([tenant_id])


@pytest.mark.integration
async def test_decision_evidence_is_tenant_isolated() -> None:
    """跨租户按 ID 读取必须 404；别的租户的中标记录也不能混进本租户证据。"""
    tenant_id, _, email = await seed_tenant_user("decision-evidence-owner")
    other_tenant_id, _, other_email = await seed_tenant_user("decision-evidence-outsider")
    try:
        # 另一个租户有同采购人的 4 条中标记录
        await _seed_awards(
            other_tenant_id,
            buyer=BUYER,
            rows=[
                ("外部供应商甲", "1200000", 30),
                ("外部供应商乙", "1000000", 100),
                ("外部供应商丙", "900000", 200),
                ("外部供应商丁", "1100000", 300),
            ],
        )
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = await _login(client, email)
            project_id = await _create_project(client, headers, name="本租户项目")
            uploaded = await client.post(
                f"/api/v1/projects/{project_id}/documents",
                files={"file": ("tender.txt", TENDER.encode(), "text/plain")},
                headers=headers,
            )
            assert uploaded.status_code == 200, uploaded.text
            await _link_buyer(project_id, tenant_id=tenant_id, buyer=BUYER)

            mine = await client.get(
                f"/api/v1/projects/{project_id}/decision-evidence", headers=headers
            )
            assert mine.status_code == 200, mine.text
            items = {item["key"]: item for item in mine.json()["items"]}
            # 别的租户有 4 条同采购人记录，但本租户一条都没有
            assert items["buyer_history"]["scored"] is False
            assert items["buyer_history"]["scope"]["tenant_id"] == str(tenant_id)
            assert items["competition"]["metrics"]["distinct_winners"] == 0

            other_headers = await _login(client, other_email)
            cross = await client.get(
                f"/api/v1/projects/{project_id}/decision-evidence", headers=other_headers
            )
            assert cross.status_code == 404, cross.text
            assert "decision-evidence" not in cross.text
            assert str(project_id) not in cross.text
    finally:
        await cleanup_tenants([tenant_id, other_tenant_id])


@pytest.mark.integration
async def test_decision_card_has_competition_dimension_with_evidence_or_explicit_gap() -> None:
    """决策卡必须带 competition 维度：有历史数据则计分，无数据则 scored=false 且不计权。"""
    tenant_id, _, email = await seed_tenant_user("decision-evidence-card")
    try:
        await _seed_awards(
            tenant_id,
            buyer=BUYER,
            rows=[
                ("供应商甲", "1200000", 30),
                ("供应商乙", "900000", 120),
                ("供应商丙", "950000", 400),
            ],
        )
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = await _login(client, email)
            project_id = await _create_project(client, headers, name="竞争维度项目")
            uploaded = await client.post(
                f"/api/v1/projects/{project_id}/documents",
                files={"file": ("tender.txt", TENDER.encode(), "text/plain")},
                headers=headers,
            )
            assert uploaded.status_code == 200, uploaded.text

            # 情形一：没有采购人 -> 竞争情报不可评分，但维度必须在场
            blank = await client.post(
                f"/api/v1/projects/{project_id}/decision-card", json={"inputs": {}}, headers=headers
            )
            assert blank.status_code == 200, blank.text
            blank_dimensions = {row["key"]: row for row in blank.json()["dimensions"]}
            assert "competition" in blank_dimensions
            assert blank_dimensions["competition"]["scored"] is False
            assert blank_dimensions["competition"]["score"] is None
            # 维度 source 标数据来源（award-records）；是否缺数据看 source_labels
            assert blank_dimensions["competition"]["source"] == "award-records"
            assert blank.json()["source_labels"]["competition"] == "missing"
            assert "采购人" in blank_dimensions["competition"]["reason"]

            # 情形二：关联采购人且有 3 条中标记录 -> 计分，source=award-records
            await _link_buyer(project_id, tenant_id=tenant_id, buyer=BUYER)
            scored = await client.post(
                f"/api/v1/projects/{project_id}/decision-card", json={"inputs": {}}, headers=headers
            )
            assert scored.status_code == 200, scored.text
            body = scored.json()
            dimensions = {row["key"]: row for row in body["dimensions"]}
            competition = dimensions["competition"]
            assert competition["scored"] is True, competition
            assert competition["score"] is not None
            assert competition["weight"] == 10
            assert competition["source"] == "award-records"
            assert "中标供应商" in competition["reason"]
            # 六维权重合计仍是 100，且历史轮次权重表仍可解释
            total_weight = sum(float(row["weight"]) for row in body["dimensions"])
            assert total_weight == 100, body["dimensions"]
            assert body["source_labels"]["competition"] == "award-records"
            assert body["engine_version"] == "decision-v2"
    finally:
        await cleanup_tenants([tenant_id])


@pytest.mark.integration
async def test_decision_card_tolerates_legacy_five_dimension_weights() -> None:
    """老租户存过五维权重表时，competition 必须回落默认值，不能 KeyError/静默跳过。"""
    from app.modules.system.models import TenantSetting

    tenant_id, _, email = await seed_tenant_user("decision-evidence-legacy-weights")
    try:
        async with SessionLocal() as session:
            session.add(
                TenantSetting(
                    tenant_id=tenant_id,
                    key="decision_weights",
                    value={
                        "qualification": 30,
                        "economics": 25,
                        "cost": 20,
                        "risk": 15,
                        "resource": 10,
                    },
                )
            )
            await session.commit()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = await _login(client, email)
            project_id = await _create_project(client, headers, name="旧权重项目")
            uploaded = await client.post(
                f"/api/v1/projects/{project_id}/documents",
                files={"file": ("tender.txt", TENDER.encode(), "text/plain")},
                headers=headers,
            )
            assert uploaded.status_code == 200, uploaded.text
            card = await client.post(
                f"/api/v1/projects/{project_id}/decision-card", json={"inputs": {}}, headers=headers
            )
            assert card.status_code == 200, card.text
            dimensions = {row["key"]: row for row in card.json()["dimensions"]}
            assert dimensions["competition"]["weight"] == 10
            assert dimensions["qualification"]["weight"] == 30
    finally:
        await cleanup_tenants([tenant_id])


@pytest.mark.integration
async def test_decision_evidence_requires_project_read_permission() -> None:
    """project:read 才能读证据链；未登录一律 401。"""
    tenant_id, _, email = await seed_tenant_user("decision-evidence-perm")
    _, _, compliance_email = await seed_tenant_user(
        "decision-evidence-compliance", "compliance", tenant_id
    )
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = await _login(client, email)
            project_id = await _create_project(client, headers, name="权限项目")
            compliance_headers = await _login(client, compliance_email)
            response = await client.get(
                f"/api/v1/projects/{project_id}/decision-evidence",
                headers=compliance_headers,
            )
            assert response.status_code == 200, response.text

            anonymous = await client.get(
                f"/api/v1/projects/{project_id}/decision-evidence"
            )
            assert anonymous.status_code == 401, anonymous.text
    finally:
        await cleanup_tenants([tenant_id])
