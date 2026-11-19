from io import BytesIO

import pytest
from httpx import ASGITransport, AsyncClient
from openpyxl import load_workbook  # type: ignore[import-untyped]

from app.main import app
from tests.support import cleanup_tenants, login, seed_tenant_user


@pytest.mark.integration
async def test_readiness_checklist_init_confirm_export_and_template_guard() -> None:
    tenant_id, _, email = await seed_tenant_user("readiness")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = await login(client, email)
            project = await client.post(
                "/api/v1/projects", json={"name": "交标清单项目"}, headers=headers
            )
            project_id = project.json()["id"]

            created = await client.get(
                f"/api/v1/projects/{project_id}/readiness", headers=headers
            )
            assert created.status_code == 200, created.text
            body = created.json()
            assert body["template"]["code"] == "it_integration"
            assert body["summary"]["total"] >= 30
            assert body["summary"]["blocking_open"] >= 1
            assert any(item["source_type"] == "auto" for item in body["items"])
            assert any(item["source_type"] == "manual" for item in body["items"])

            manual_blocking = next(
                item
                for item in body["items"]
                if item["source_type"] == "manual" and item["is_blocking"]
            )
            missing_evidence = await client.patch(
                f"/api/v1/projects/{project_id}/readiness/{manual_blocking['id']}",
                json={"status": "done"},
                headers=headers,
            )
            assert missing_evidence.status_code == 422, missing_evidence.text

            done = await client.patch(
                f"/api/v1/projects/{project_id}/readiness/{manual_blocking['id']}",
                json={
                    "status": "done",
                    "evidence_refs": ["营业执照-2026.pdf"],
                    "note": "已核对原件",
                },
                headers=headers,
            )
            assert done.status_code == 200, done.text
            assert done.json()["status"] == "done"
            assert done.json()["confirmed_at"]

            auto_item = next(item for item in body["items"] if item["source_type"] == "auto")
            auto_edit = await client.patch(
                f"/api/v1/projects/{project_id}/readiness/{auto_item['id']}",
                json={"status": "done"},
                headers=headers,
            )
            assert auto_edit.status_code == 422, auto_edit.text

            optional = next(
                item
                for item in body["items"]
                if item["source_type"] == "manual" and not item["is_blocking"]
            )
            no_reason = await client.patch(
                f"/api/v1/projects/{project_id}/readiness/{optional['id']}",
                json={"status": "not_applicable"},
                headers=headers,
            )
            assert no_reason.status_code == 422, no_reason.text
            na = await client.patch(
                f"/api/v1/projects/{project_id}/readiness/{optional['id']}",
                json={"status": "not_applicable", "waived_reason": "本项目不适用"},
                headers=headers,
            )
            assert na.status_code == 200, na.text
            assert na.json()["status"] == "not_applicable"

            refreshed = await client.post(
                f"/api/v1/projects/{project_id}/readiness/refresh", headers=headers
            )
            assert refreshed.status_code == 200, refreshed.text
            assert refreshed.json()["summary"]["done"] >= 1

            exported = await client.get(
                f"/api/v1/projects/{project_id}/readiness/export", headers=headers
            )
            assert exported.status_code == 200, exported.text
            assert exported.content.startswith(b"PK")
            workbook = load_workbook(BytesIO(exported.content))
            assert workbook["Readiness"].max_row >= 2

            other = await client.post(
                "/api/v1/projects", json={"name": "政采清单项目"}, headers=headers
            )
            other_id = other.json()["id"]
            gov = await client.get(
                f"/api/v1/projects/{other_id}/readiness",
                params={"template_code": "gov_procurement_services"},
                headers=headers,
            )
            assert gov.status_code == 200, gov.text
            assert gov.json()["template"]["code"] == "gov_procurement_services"

            conflict = await client.get(
                f"/api/v1/projects/{project_id}/readiness",
                params={"template_code": "engineering"},
                headers=headers,
            )
            assert conflict.status_code == 409, conflict.text
    finally:
        await cleanup_tenants([tenant_id])
