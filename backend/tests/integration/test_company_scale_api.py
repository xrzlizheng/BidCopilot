"""企业划型：创建/更新时自动计算，人工确认值优先。

覆盖一个真实 bug：创建时构造 Company 未传入划型原始指标，导致
_apply_scale_inputs 读到全是 None，得出「缺指标」的错误结论。
"""

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from tests.support import cleanup_tenants, login, seed_tenant_user


@pytest.mark.integration
async def test_company_scale_computed_on_create() -> None:
    tenant_id, _, email = await seed_tenant_user("scale-create")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = await login(client, email)
            created = await client.post(
                "/api/v1/companies",
                json={
                    "name": "大型软件公司",
                    "industry": "软件和信息技术服务业",
                    "employee_count": 500,
                    "annual_revenue": 20000,
                },
                headers=headers,
            )
            assert created.status_code == 201, created.text
            company_id = created.json()["id"]
            assert created.json()["enterprise_scale"] == "大型"

            scale = await client.get(
                f"/api/v1/companies/{company_id}/scale", headers=headers
            )
            assert scale.status_code == 200, scale.text
            body = scale.json()
            assert body["scale"] == "大型"
            assert body["is_sme"] is False
            assert body["missing"] == []
    finally:
        await cleanup_tenants([tenant_id])


@pytest.mark.integration
async def test_small_company_recognized_as_sme() -> None:
    tenant_id, _, email = await seed_tenant_user("scale-small")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = await login(client, email)
            created = await client.post(
                "/api/v1/companies",
                json={
                    "name": "小型软件公司",
                    "industry": "软件和信息技术服务业",
                    "employee_count": 50,
                    "annual_revenue": 600,
                },
                headers=headers,
            )
            assert created.status_code == 201, created.text
            company_id = created.json()["id"]
            scale = (
                await client.get(f"/api/v1/companies/{company_id}/scale", headers=headers)
            ).json()
            assert scale["scale"] == "小型"
            assert scale["is_sme"] is True
    finally:
        await cleanup_tenants([tenant_id])


@pytest.mark.integration
async def test_manual_scale_is_not_overwritten_by_recompute() -> None:
    """人工确认的划型不能被后续自动重算覆盖——人工判断优先级更高。"""
    tenant_id, _, email = await seed_tenant_user("scale-manual")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = await login(client, email)
            created = await client.post(
                "/api/v1/companies",
                json={
                    "name": "人工确认公司",
                    "industry": "软件和信息技术服务业",
                    "employee_count": 500,
                    "annual_revenue": 20000,
                    "enterprise_scale": "中型",
                },
                headers=headers,
            )
            company_id = created.json()["id"]
            assert created.json()["enterprise_scale"] == "中型"
            assert created.json()["enterprise_scale_source"] == "manual"

            # 只改营收，不传 enterprise_scale：人工值应保留
            patched = await client.patch(
                f"/api/v1/companies/{company_id}",
                json={"annual_revenue": 30000},
                headers=headers,
            )
            assert patched.json()["enterprise_scale"] == "中型"
            assert patched.json()["enterprise_scale_source"] == "manual"
    finally:
        await cleanup_tenants([tenant_id])


@pytest.mark.integration
async def test_missing_metrics_reports_what_is_missing() -> None:
    tenant_id, _, email = await seed_tenant_user("scale-missing")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = await login(client, email)
            created = await client.post(
                "/api/v1/companies",
                json={"name": "指标缺失公司", "industry": "软件和信息技术服务业"},
                headers=headers,
            )
            company_id = created.json()["id"]
            scale = (
                await client.get(f"/api/v1/companies/{company_id}/scale", headers=headers)
            ).json()
            assert scale["scale"] is None
            assert "营业收入" in scale["missing"]
            assert "从业人员" in scale["missing"]
    finally:
        await cleanup_tenants([tenant_id])


@pytest.mark.integration
async def test_clearing_manual_scale_restores_auto_compute() -> None:
    """前端「保存并计算」传 enterprise_scale=null，应恢复自动计算而不是保留人工值。"""
    tenant_id, _, email = await seed_tenant_user("scale-reset")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = await login(client, email)
            created = await client.post(
                "/api/v1/companies",
                json={
                    "name": "重置划型公司",
                    "industry": "软件和信息技术服务业",
                    "employee_count": 500,
                    "annual_revenue": 20000,
                    "enterprise_scale": "小型",
                },
                headers=headers,
            )
            company_id = created.json()["id"]
            assert created.json()["enterprise_scale_source"] == "manual"

            # 传 null 表示放弃人工结论，按指标重算
            reset = await client.patch(
                f"/api/v1/companies/{company_id}",
                json={"enterprise_scale": None, "annual_revenue": 25000},
                headers=headers,
            )
            body = reset.json()
            assert body["enterprise_scale"] == "大型", body
            assert body["enterprise_scale_source"] == "auto"
    finally:
        await cleanup_tenants([tenant_id])


@pytest.mark.integration
async def test_scale_endpoint_returns_basis_for_review() -> None:
    """依据必须能复核：带上指标数值与行业名，用户才知道凭什么这么判。"""
    tenant_id, _, email = await seed_tenant_user("scale-basis")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = await login(client, email)
            created = await client.post(
                "/api/v1/companies",
                json={
                    "name": "依据展示公司",
                    "industry": "物业管理",
                    "employee_count": 150,
                    "annual_revenue": 600,
                },
                headers=headers,
            )
            company_id = created.json()["id"]
            scale = (
                await client.get(f"/api/v1/companies/{company_id}/scale", headers=headers)
            ).json()
            assert scale["scale"] == "小型"
            assert "物业管理" in scale["basis"]
            assert "150" in scale["basis"]
    finally:
        await cleanup_tenants([tenant_id])


@pytest.mark.integration
async def test_scale_impact_flags_active_projects_on_sme_boundary_cross() -> None:
    """中型→大型会让人丧失中小企业项目资格，必须列出受影响在投项目。"""
    tenant_id, _, email = await seed_tenant_user("scale-impact")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = await login(client, email)
            created = await client.post(
                "/api/v1/companies",
                json={
                    "name": "边界跨越公司",
                    "industry": "软件和信息技术服务业",
                    "employee_count": 600,
                    "annual_revenue": 25000,
                },
                headers=headers,
            )
            company_id = created.json()["id"]
            project = await client.post(
                "/api/v1/projects",
                json={"name": "在投项目", "company_id": company_id},
                headers=headers,
            )
            assert project.status_code == 201, project.text

            impact = (
                await client.get(
                    f"/api/v1/companies/{company_id}/scale-impact",
                    params={"previous_scale": "中型"},
                    headers=headers,
                )
            ).json()
            assert impact["crossed_boundary"] is True
            assert impact["current_scale"] == "大型"
            assert impact["needs_review"] is True
            assert len(impact["affected_projects"]) == 1
            assert impact["affected_projects"][0]["name"] == "在投项目"
            assert "虚假材料" in impact["message"]
    finally:
        await cleanup_tenants([tenant_id])


@pytest.mark.integration
async def test_scale_impact_ignores_internal_change_within_sme() -> None:
    """小型→中型同属中小企业，不该报警——否则用户会对提醒脱敏。"""
    tenant_id, _, email = await seed_tenant_user("scale-noimpact")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = await login(client, email)
            created = await client.post(
                "/api/v1/companies",
                json={
                    "name": "内部变动公司",
                    "industry": "软件和信息技术服务业",
                    "employee_count": 120,
                    "annual_revenue": 2000,
                },
                headers=headers,
            )
            company_id = created.json()["id"]
            await client.post(
                "/api/v1/projects",
                json={"name": "内部变动在投项目", "company_id": company_id},
                headers=headers,
            )
            impact = (
                await client.get(
                    f"/api/v1/companies/{company_id}/scale-impact",
                    params={"previous_scale": "小型"},
                    headers=headers,
                )
            ).json()
            assert impact["current_scale"] == "中型"
            assert impact["crossed_boundary"] is False
            assert impact["needs_review"] is False
            assert impact["affected_projects"] == []
    finally:
        await cleanup_tenants([tenant_id])


@pytest.mark.integration
async def test_scale_impact_excludes_closed_projects() -> None:
    """已流标/中标项目的提醒没有挽回余地，不该混进清单。"""
    tenant_id, _, email = await seed_tenant_user("scale-closed")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = await login(client, email)
            created = await client.post(
                "/api/v1/companies",
                json={
                    "name": "已关闭项目公司",
                    "industry": "软件和信息技术服务业",
                    "employee_count": 600,
                    "annual_revenue": 25000,
                },
                headers=headers,
            )
            company_id = created.json()["id"]
            project = await client.post(
                "/api/v1/projects",
                json={"name": "已中标项目", "company_id": company_id},
                headers=headers,
            )
            project_id = project.json()["id"]
            # ProjectUpdate 里没有 status 字段（改状态走专门的接口），
            # 这里直接改库，目的只是验证影响评估的过滤条件。
            from uuid import UUID

            from sqlalchemy import update

            from app.db.session import SessionLocal
            from app.modules.projects.models import BidProject

            async with SessionLocal() as session:
                await session.execute(
                    update(BidProject)
                    .where(BidProject.id == UUID(project_id))
                    .values(status="won")
                )
                await session.commit()

            impact = (
                await client.get(
                    f"/api/v1/companies/{company_id}/scale-impact",
                    params={"previous_scale": "中型"},
                    headers=headers,
                )
            ).json()
            assert impact["affected_projects"] == []
            assert impact["needs_review"] is False
    finally:
        await cleanup_tenants([tenant_id])
