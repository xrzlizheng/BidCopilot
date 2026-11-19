"""项目状态流转：在投 / 已中标 / 未中标。

状态是所有下游判断的基础（在投提醒、就绪校验、依赖划型的复核清单），
所以必须走专门接口 + 状态机校验，不能让 PATCH 随手改。
"""

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from tests.support import cleanup_tenants, login, seed_tenant_user


@pytest.mark.integration
async def test_status_transition_active_to_won() -> None:
    tenant_id, _, email = await seed_tenant_user("status-won")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = await login(client, email)
            project = await client.post(
                "/api/v1/projects", json={"name": "状态流转项目"}, headers=headers
            )
            project_id = project.json()["id"]
            assert project.json()["status"] == "active"

            changed = await client.post(
                f"/api/v1/projects/{project_id}/status",
                json={"status": "won"},
                headers=headers,
            )
            assert changed.status_code == 200, changed.text
            assert changed.json()["status"] == "won"
    finally:
        await cleanup_tenants([tenant_id])


@pytest.mark.integration
async def test_lost_requires_reason() -> None:
    """未中标必须写原因——复盘和「同类项目要不要再投」全靠这个。"""
    tenant_id, _, email = await seed_tenant_user("status-lost")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = await login(client, email)
            project = await client.post(
                "/api/v1/projects", json={"name": "未中标项目"}, headers=headers
            )
            project_id = project.json()["id"]

            missing = await client.post(
                f"/api/v1/projects/{project_id}/status",
                json={"status": "lost"},
                headers=headers,
            )
            assert missing.status_code == 422, missing.text

            ok = await client.post(
                f"/api/v1/projects/{project_id}/status",
                json={"status": "lost", "reason": "技术分落后 8 分"},
                headers=headers,
            )
            assert ok.status_code == 200, ok.text
            assert ok.json()["status"] == "lost"
    finally:
        await cleanup_tenants([tenant_id])


@pytest.mark.integration
async def test_won_is_terminal() -> None:
    """已中标不能再改——中标结果不能因为点错就抹掉，要重开应新建项目。"""
    tenant_id, _, email = await seed_tenant_user("status-terminal")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = await login(client, email)
            project = await client.post(
                "/api/v1/projects", json={"name": "终态项目"}, headers=headers
            )
            project_id = project.json()["id"]
            await client.post(
                f"/api/v1/projects/{project_id}/status", json={"status": "won"}, headers=headers
            )
            back = await client.post(
                f"/api/v1/projects/{project_id}/status",
                json={"status": "active"},
                headers=headers,
            )
            assert back.status_code == 422, back.text
    finally:
        await cleanup_tenants([tenant_id])


@pytest.mark.integration
async def test_lost_can_reopen_as_active() -> None:
    """未中标项目允许重新激活（补充投标、二次招标）。"""
    tenant_id, _, email = await seed_tenant_user("status-reopen")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = await login(client, email)
            project = await client.post(
                "/api/v1/projects", json={"name": "二次投标项目"}, headers=headers
            )
            project_id = project.json()["id"]
            await client.post(
                f"/api/v1/projects/{project_id}/status",
                json={"status": "lost", "reason": "废标重招"},
                headers=headers,
            )
            reopened = await client.post(
                f"/api/v1/projects/{project_id}/status",
                json={"status": "active"},
                headers=headers,
            )
            assert reopened.status_code == 200, reopened.text
            assert reopened.json()["status"] == "active"
    finally:
        await cleanup_tenants([tenant_id])


@pytest.mark.integration
async def test_scale_history_records_change() -> None:
    """划型变更要留下可查询的历史——声明函纠纷时这是自证材料。"""
    tenant_id, _, email = await seed_tenant_user("scale-hist")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = await login(client, email)
            created = await client.post(
                "/api/v1/companies",
                json={
                    "name": "历史记录公司",
                    "industry": "software",
                    "employee_count": 120,
                    "annual_revenue": 2000,
                },
                headers=headers,
            )
            company_id = created.json()["id"]
            # 改成大型，触发一次有意义的变更
            await client.patch(
                f"/api/v1/companies/{company_id}",
                json={"employee_count": 600, "annual_revenue": 30000},
                headers=headers,
            )
            history = (
                await client.get(
                    f"/api/v1/companies/{company_id}/scale-history", headers=headers
                )
            ).json()
            assert history, "变更后应有历史记录"
            assert history[0]["to"] == "大型"
            assert history[0]["crossed_boundary"] is True
    finally:
        await cleanup_tenants([tenant_id])


@pytest.mark.integration
async def test_decision_card_snapshots_enterprise_scale() -> None:
    """决策卡要落划型快照，半年后还能回答「当初为何判 No-Go」。"""
    tenant_id, _, email = await seed_tenant_user("scale-snapshot")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = await login(client, email)
            company = await client.post(
                "/api/v1/companies",
                json={
                    "name": "快照公司",
                    "industry": "software",
                    "employee_count": 600,
                    "annual_revenue": 25000,
                },
                headers=headers,
            )
            project = await client.post(
                "/api/v1/projects",
                json={"name": "快照项目", "company_id": company.json()["id"]},
                headers=headers,
            )
            project_id = project.json()["id"]
            await client.post(
                f"/api/v1/projects/{project_id}/documents",
                files={
                    "file": (
                        "t.txt",
                        "本项目专门面向中小企业，须提供中小企业声明函。".encode(),
                        "text/plain",
                    )
                },
                headers=headers,
            )
            card = await client.post(
                f"/api/v1/projects/{project_id}/decision-card",
                json={"inputs": {}},
                headers=headers,
            )
            assert card.status_code == 200, card.text
            snapshot = card.json().get("profile_snapshot") or {}
            scale = snapshot.get("enterprise_scale") or {}
            assert scale.get("scale") == "大型"
            assert scale.get("is_sme") is False
    finally:
        await cleanup_tenants([tenant_id])
