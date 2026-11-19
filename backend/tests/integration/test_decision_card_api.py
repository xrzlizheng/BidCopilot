import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from tests.support import cleanup_tenants, login, seed_tenant_user

TENDER = """
某市政务云运维服务项目招标文件
项目预算：人民币 150 万元。
投标截止时间：2027-01-08 10:00。
投标人须具备信息系统集成资质。
系统应支持双活架构。
"""


@pytest.mark.integration
async def test_legacy_decision_writes_run_without_polluting_new_card() -> None:
    tenant_id, _, email = await seed_tenant_user("decision-legacy")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = await login(client, email)
            project = await client.post(
                "/api/v1/projects", json={"name": "旧决策项目"}, headers=headers
            )
            project_id = project.json()["id"]
            uploaded = await client.post(
                f"/api/v1/projects/{project_id}/documents",
                files={"file": ("tender.txt", TENDER.encode(), "text/plain")},
                headers=headers,
            )
            assert uploaded.status_code == 200, uploaded.text

            legacy = await client.post(
                f"/api/v1/projects/{project_id}/decision", headers=headers
            )
            assert legacy.status_code == 200, legacy.text
            old_get = await client.get(
                f"/api/v1/projects/{project_id}/decision", headers=headers
            )
            assert old_get.status_code == 200, old_get.text

            runs = await client.get(
                f"/api/v1/projects/{project_id}/decision-card/runs", headers=headers
            )
            assert runs.status_code == 200, runs.text
            assert len(runs.json()) == 1
            assert runs.json()[0]["engine_mode"] == "legacy"
            assert runs.json()[0]["computed_status"] == old_get.json()["status"]

            card = await client.get(
                f"/api/v1/projects/{project_id}/decision-card", headers=headers
            )
            assert card.status_code == 404, "legacy run must not masquerade as full decision card"
    finally:
        await cleanup_tenants([tenant_id])


@pytest.mark.integration
async def test_decision_card_dual_cost_veto_and_human_confirmation() -> None:
    tenant_id, _, email = await seed_tenant_user("decision-card")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = await login(client, email)
            project = await client.post(
                "/api/v1/projects", json={"name": "决策卡项目"}, headers=headers
            )
            project_id = project.json()["id"]
            uploaded = await client.post(
                f"/api/v1/projects/{project_id}/documents",
                files={"file": ("tender.txt", TENDER.encode(), "text/plain")},
                headers=headers,
            )
            assert uploaded.status_code == 200, uploaded.text

            missing_cost = await client.post(
                f"/api/v1/projects/{project_id}/decision-card",
                json={"inputs": {}},
                headers=headers,
            )
            assert missing_cost.status_code == 200, missing_cost.text
            assert missing_cost.json()["status"] != "Go", "缺成本不得直接给 Go"

            first = await client.post(
                f"/api/v1/projects/{project_id}/decision-card",
                json={
                    "inputs": {
                        "quote_amount": 1000000,
                        "estimated_cost": 900000,
                        "tax_rate": 0.09,
                    }
                },
                headers=headers,
            )
            assert first.status_code == 200, first.text
            body = first.json()
            assert body["run_no"] == 2
            assert body["score"] is not None
            assert body["status"] != "Go", body
            keys = {row["key"] for row in body["dimensions"]}
            assert {"qualification", "cost", "risk", "resource"} <= keys
            assert body["inputs"]["quote_amount"] == "1000000.00" or body["inputs"]["quote_amount"] == "1000000"

            second = await client.post(
                f"/api/v1/projects/{project_id}/decision-card",
                json={
                    "inputs": {
                        "quote_amount": 1000000,
                        "estimated_cost": 2000000,
                        "tax_rate": 0.09,
                    }
                },
                headers=headers,
            )
            assert second.status_code == 200, second.text
            assert second.json()["computed_status"] == "No-Go"
            assert any(
                item["code"] == "BELOW_COST" for item in second.json()["vetoes"]
            )

            latest = await client.get(
                f"/api/v1/projects/{project_id}/decision-card", headers=headers
            )
            assert latest.status_code == 200, latest.text
            assert latest.json()["run_no"] == 3

            no_reason = await client.patch(
                f"/api/v1/projects/{project_id}/decision-card",
                json={"human_status": "Go"},
                headers=headers,
            )
            assert no_reason.status_code == 422, no_reason.text

            confirmed = await client.patch(
                f"/api/v1/projects/{project_id}/decision-card",
                json={"human_status": "Go", "override_reason": "战略项目，接受低毛利"},
                headers=headers,
            )
            assert confirmed.status_code == 200, confirmed.text
            assert confirmed.json()["status"] == "Go"
            assert confirmed.json()["human_status"] == "Go"

            runs = await client.get(
                f"/api/v1/projects/{project_id}/decision-card/runs", headers=headers
            )
            assert runs.status_code == 200, runs.text
            assert len(runs.json()) == 3
            assert runs.json()[0]["status"] == "Go"
    finally:
        await cleanup_tenants([tenant_id])


RISKY_TENDER = """
某市政务云运维服务项目招标文件
公告发布时间：2026年10月8日
项目预算：人民币 150 万元。
投标截止时间：2027-01-08 10:00。
本项目专门面向中小企业，须提供中小企业声明函。
投标人须具有近三年不少于 2 个类似项目业绩。
本项目不接受联合体投标。
投标人须在本市注册并缴纳社保满一年。
"""


@pytest.mark.integration
async def test_decision_card_penalizes_bid_risks() -> None:
    """废标风险必须拉低风险维度得分，并在理由里点名，不能静默忽略。"""
    tenant_id, _, email = await seed_tenant_user("decision-risk")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = await login(client, email)
            project = await client.post(
                "/api/v1/projects", json={"name": "风险决策项目"}, headers=headers
            )
            assert project.status_code == 201, project.text
            project_id = project.json()["id"]
            uploaded = await client.post(
                f"/api/v1/projects/{project_id}/documents",
                files={"file": ("tender.txt", RISKY_TENDER.encode(), "text/plain")},
                headers=headers,
            )
            assert uploaded.status_code == 200, uploaded.text

            constraint = await client.get(
                f"/api/v1/projects/{project_id}/constraints", headers=headers
            )
            assert constraint.status_code == 200, constraint.text
            risks = constraint.json()["payload"]["bid_risks"]
            assert risks["summary"]["blocker"] >= 2, risks
            assert risks["flags"]["sme_only"] is True
            assert risks["flags"]["consortium_allowed"] is False

            card = await client.post(
                f"/api/v1/projects/{project_id}/decision-card",
                json={"inputs": {}},
                headers=headers,
            )
            assert card.status_code == 200, card.text
            payload = card.json()
            risk = next(
                item for item in payload["dimensions"] if item["key"] == "risk"
            )
            assert risk["scored"] is True
            assert "废标风险" in risk["reason"] or "中小企业" in risk["reason"], risk
            # 有废标项时风险维度必须低于满分
            assert float(risk["score"]) < 100
    finally:
        await cleanup_tenants([tenant_id])

