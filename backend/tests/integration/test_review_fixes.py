from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from tests.support import cleanup_tenants, login, sample_tender_docx, seed_tenant_user


async def _create_kb(
    client: AsyncClient, headers: dict[str, str], name: str, visibility: str = "shared"
) -> dict:
    response = await client.post(
        "/api/v1/knowledge/bases",
        json={"name": name, "scope": "tenant", "visibility": visibility},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _upload_doc(
    client: AsyncClient, headers: dict[str, str], kb_id: str, title: str
) -> dict:
    response = await client.post(
        "/api/v1/knowledge/documents",
        files={
            "file": (
                "review.docx",
                sample_tender_docx(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
        data={"title": title, "kind": "policy", "kb_id": kb_id},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.integration
async def test_acl_revoke_cannot_cross_resource() -> None:
    tenant_id, _, admin_email = await seed_tenant_user("review-acl-admin")
    _, engineer_id, engineer_email = await seed_tenant_user(
        "review-acl-engineer", "bid_engineer", tenant_id
    )
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            admin = await login(client, admin_email)
            other_kb = await _create_kb(client, admin, "另一共享库")
            target_kb = await _create_kb(client, admin, "目标共享库")
            granted = await client.post(
                f"/api/v1/knowledge/bases/{target_kb['id']}/acl",
                json={
                    "subject_type": "user",
                    "subject_id": str(engineer_id),
                    "role": "viewer",
                },
                headers=admin,
            )
            assert granted.status_code == 201, granted.text
            acl_id = granted.json()["id"]

            crossed = await client.delete(
                f"/api/v1/knowledge/bases/{other_kb['id']}/acl/{acl_id}",
                headers=admin,
            )
            assert crossed.status_code == 404, crossed.text

            rows = await client.get(
                f"/api/v1/knowledge/bases/{target_kb['id']}/acl", headers=admin
            )
            assert rows.status_code == 200
            assert acl_id in {row["id"] for row in rows.json()}

            engineer = await login(client, engineer_email)
            visible = await client.get(
                "/api/v1/knowledge/bases", headers=engineer
            )
            assert target_kb["id"] in {row["id"] for row in visible.json()}
    finally:
        await cleanup_tenants([tenant_id])


@pytest.mark.integration
async def test_document_acl_restricts_write_actions() -> None:
    tenant_id, _, admin_email = await seed_tenant_user("review-doc-acl-admin")
    _, engineer_id, engineer_email = await seed_tenant_user(
        "review-doc-acl-engineer", "bid_engineer", tenant_id
    )
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            admin = await login(client, admin_email)
            kb = await _create_kb(client, admin, "文档 ACL 库")
            kb_grant = await client.post(
                f"/api/v1/knowledge/bases/{kb['id']}/acl",
                json={
                    "subject_type": "user",
                    "subject_id": str(engineer_id),
                    "role": "editor",
                },
                headers=admin,
            )
            assert kb_grant.status_code == 201, kb_grant.text
            document = await _upload_doc(client, admin, kb["id"], "文档 ACL 测试")
            doc_grant = await client.post(
                f"/api/v1/knowledge/documents/{document['id']}/acl",
                json={
                    "subject_type": "user",
                    "subject_id": str(engineer_id),
                    "role": "viewer",
                },
                headers=admin,
            )
            assert doc_grant.status_code == 201, doc_grant.text

            engineer = await login(client, engineer_email)
            readable = await client.get(
                f"/api/v1/knowledge/documents/{document['id']}", headers=engineer
            )
            assert readable.status_code == 200, readable.text
            writable = await client.patch(
                f"/api/v1/knowledge/documents/{document['id']}",
                json={"title": "越权改名"},
                headers=engineer,
            )
            assert writable.status_code == 403, writable.text
    finally:
        await cleanup_tenants([tenant_id])


@pytest.mark.integration
async def test_expired_document_acl_does_not_lock_document() -> None:
    tenant_id, _, admin_email = await seed_tenant_user("review-expired-acl-admin")
    _, engineer_id, engineer_email = await seed_tenant_user(
        "review-expired-acl-engineer", "bid_engineer", tenant_id
    )
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            admin = await login(client, admin_email)
            bases = await client.get("/api/v1/knowledge/bases", headers=admin)
            default_kb = next(
                row
                for row in bases.json()
                if row["scope"] == "tenant" and row["name"] == "企业知识库"
            )
            document = await _upload_doc(
                client, admin, default_kb["id"], "过期 ACL 文档"
            )
            expired_at = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
            before = await client.post(
                f"/api/v1/knowledge/documents/{document['id']}/acl",
                json={
                    "subject_type": "user",
                    "subject_id": str(engineer_id),
                    "role": "viewer",
                    "expires_at": expired_at,
                },
                headers=admin,
            )
            assert before.status_code == 201, before.text

            engineer = await login(client, engineer_email)
            readable = await client.get(
                f"/api/v1/knowledge/documents/{document['id']}", headers=engineer
            )
            assert readable.status_code == 200, readable.text
    finally:
        await cleanup_tenants([tenant_id])


@pytest.mark.integration
async def test_role_assignment_cannot_escalate_permissions() -> None:
    tenant_id, _, admin_email = await seed_tenant_user("review-role-admin")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            admin = await login(client, admin_email)
            role = await client.post(
                "/api/v1/roles",
                json={
                    "name": "仅委派角色",
                    "permissions": ["role:assign"],
                    "scope_types": ["tenant"],
                },
                headers=admin,
            )
            assert role.status_code == 201, role.text
            role_code = role.json()["code"]
            created = await client.post(
                "/api/v1/users",
                json={
                    "email": f"delegator-{tenant_id.hex[:8]}@example.com",
                    "password": "Password123!",
                    "display_name": "委派管理员",
                    "role_codes": [role_code],
                },
                headers=admin,
            )
            assert created.status_code == 201, created.text
            delegator_id = created.json()["id"]
            delegator = await login(client, created.json()["email"])

            escalated = await client.put(
                f"/api/v1/users/{delegator_id}/role-assignments",
                json={
                    "assignments": [
                        {"role_code": "tenant_admin", "scope_type": "tenant"}
                    ]
                },
                headers=delegator,
            )
            assert escalated.status_code == 403, escalated.text
    finally:
        await cleanup_tenants([tenant_id])


@pytest.mark.integration
async def test_acl_subjects_are_scoped_for_org_managers() -> None:
    tenant_id, _, admin_email = await seed_tenant_user("review-subject-admin")
    _, engineer_id, engineer_email = await seed_tenant_user(
        "review-subject-engineer", "bid_engineer", tenant_id
    )
    _, outsider_id, _ = await seed_tenant_user(
        "review-subject-outsider", "bid_engineer", tenant_id
    )
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            admin = await login(client, admin_email)
            role = await client.post(
                "/api/v1/roles",
                json={
                    "name": "部门知识管理员",
                    "permissions": ["knowledge:read", "knowledge:manage"],
                    "scope_types": ["org_unit"],
                },
                headers=admin,
            )
            assert role.status_code == 201, role.text
            org_a = await client.post(
                "/api/v1/org-units", json={"name": "部门 A", "kind": "department"}, headers=admin
            )
            org_b = await client.post(
                "/api/v1/org-units", json={"name": "部门 B", "kind": "department"}, headers=admin
            )
            assert org_a.status_code == 201 and org_b.status_code == 201
            org_a_id = org_a.json()["id"]
            org_b_id = org_b.json()["id"]
            await client.post(
                f"/api/v1/org-units/{org_a_id}/members",
                json={"user_id": str(engineer_id), "member_role": "member"},
                headers=admin,
            )
            await client.post(
                f"/api/v1/org-units/{org_b_id}/members",
                json={"user_id": str(outsider_id), "member_role": "member"},
                headers=admin,
            )
            assigned = await client.put(
                f"/api/v1/users/{engineer_id}/role-assignments",
                json={
                    "assignments": [
                        {
                            "role_code": role.json()["code"],
                            "scope_type": "org_unit",
                            "scope_id": org_a_id,
                        }
                    ]
                },
                headers=admin,
            )
            assert assigned.status_code == 200, assigned.text

            engineer = await login(client, engineer_email)
            subjects = await client.get("/api/v1/knowledge/acl-subjects", headers=engineer)
            assert subjects.status_code == 200, subjects.text
            payload = subjects.json()
            assert {row["id"] for row in payload["org_units"]} == {org_a_id}
            assert {row["id"] for row in payload["users"]} == {str(engineer_id)}
    finally:
        await cleanup_tenants([tenant_id])


@pytest.mark.integration
async def test_accessible_kb_ids_uses_bounded_queries() -> None:
    tenant_id, admin_id, admin_email = await seed_tenant_user("review-nplus1-admin")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            admin = await login(client, admin_email)
            for index in range(8):
                await _create_kb(client, admin, f"批量判权库 {index}")

        from sqlalchemy import event

        from app.db.session import SessionLocal, engine
        from app.modules.identity.service import load_user_context
        from app.modules.knowledge.access import accessible_kb_ids

        counter = {"queries": 0}

        def _count(*_args) -> None:
            counter["queries"] += 1

        event.listen(engine.sync_engine, "before_cursor_execute", _count)
        try:
            async with SessionLocal() as session:
                user = await load_user_context(session, admin_id)
                assert user is not None
                counter["queries"] = 0
                ids = await accessible_kb_ids(session, tenant_id, user)
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", _count)
        assert len(ids) == 8
        assert counter["queries"] <= 6, counter
    finally:
        await cleanup_tenants([tenant_id])


@pytest.mark.integration
async def test_org_acl_include_descendants() -> None:
    tenant_id, _, admin_email = await seed_tenant_user("review-desc-admin")
    _, engineer_id, engineer_email = await seed_tenant_user(
        "review-desc-engineer", "bid_engineer", tenant_id
    )
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            admin = await login(client, admin_email)
            parent = await client.post(
                "/api/v1/org-units", json={"name": "父部门", "kind": "department"}, headers=admin
            )
            assert parent.status_code == 201, parent.text
            parent_id = parent.json()["id"]
            child = await client.post(
                "/api/v1/org-units",
                json={"name": "子部门", "kind": "department", "parent_id": parent_id},
                headers=admin,
            )
            assert child.status_code == 201, child.text
            child_id = child.json()["id"]
            await client.post(
                f"/api/v1/org-units/{child_id}/members",
                json={"user_id": str(engineer_id), "member_role": "member"},
                headers=admin,
            )
            kb = await _create_kb(client, admin, "子部门继承库")
            granted = await client.post(
                f"/api/v1/knowledge/bases/{kb['id']}/acl",
                json={
                    "subject_type": "org_unit",
                    "subject_id": parent_id,
                    "role": "viewer",
                    "include_descendants": False,
                },
                headers=admin,
            )
            assert granted.status_code == 201, granted.text
            assert granted.json()["include_descendants"] is False

            engineer = await login(client, engineer_email)
            before = await client.get("/api/v1/knowledge/bases", headers=engineer)
            assert kb["id"] not in {row["id"] for row in before.json()}

            updated = await client.post(
                f"/api/v1/knowledge/bases/{kb['id']}/acl",
                json={
                    "subject_type": "org_unit",
                    "subject_id": parent_id,
                    "role": "viewer",
                    "include_descendants": True,
                },
                headers=admin,
            )
            assert updated.status_code == 201, updated.text
            assert updated.json()["include_descendants"] is True

            after = await client.get("/api/v1/knowledge/bases", headers=engineer)
            assert kb["id"] in {row["id"] for row in after.json()}
    finally:
        await cleanup_tenants([tenant_id])


@pytest.mark.integration
async def test_notifications_do_not_require_opportunity_capability() -> None:
    tenant_id, _, admin_email = await seed_tenant_user("review-notify-admin")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            admin = await login(client, admin_email)
            role = await client.post(
                "/api/v1/roles",
                json={
                    "name": "仅项目只读",
                    "permissions": ["project:read"],
                    "scope_types": ["tenant"],
                },
                headers=admin,
            )
            assert role.status_code == 201, role.text
            created = await client.post(
                "/api/v1/users",
                json={
                    "email": f"notify-{tenant_id.hex[:8]}@example.com",
                    "password": "Password123!",
                    "display_name": "项目只读用户",
                    "role_codes": [role.json()["code"]],
                },
                headers=admin,
            )
            assert created.status_code == 201, created.text
            user = await login(client, created.json()["email"])
            response = await client.get("/api/v1/notifications", headers=user)
            assert response.status_code == 200, response.text
            assert response.json()["items"] == []
    finally:
        await cleanup_tenants([tenant_id])


@pytest.mark.integration
async def test_generation_asset_loader_respects_department_scope() -> None:
    tenant_id, _, admin_email = await seed_tenant_user("review-asset-scope-admin")
    _, engineer_id, _ = await seed_tenant_user(
        "review-asset-scope-engineer", "bid_engineer", tenant_id
    )
    _, outsider_id, _ = await seed_tenant_user(
        "review-asset-scope-outsider", "bid_engineer", tenant_id
    )
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            admin = await login(client, admin_email)
            org = await client.post(
                "/api/v1/org-units", json={"name": "资产业务部", "kind": "department"}, headers=admin
            )
            assert org.status_code == 201, org.text
            org_id = org.json()["id"]
            await client.post(
                f"/api/v1/org-units/{org_id}/members",
                json={"user_id": str(engineer_id), "member_role": "member"},
                headers=admin,
            )
            company = await client.post(
                "/api/v1/companies", json={"name": "资产业务公司"}, headers=admin
            )
            assert company.status_code == 201, company.text
            company_id = company.json()["id"]
            dept = await client.post(
                "/api/v1/assets/qualifications",
                json={
                    "company_id": company_id,
                    "org_unit_id": org_id,
                    "name": "部门专属资质",
                },
                headers=admin,
            )
            enterprise = await client.post(
                "/api/v1/assets/qualifications",
                json={"company_id": company_id, "name": "企业共享资质"},
                headers=admin,
            )
            assert dept.status_code == 201 and enterprise.status_code == 201

            from app.db.session import SessionLocal
            from app.modules.identity.service import load_user_context
            from app.modules.projects.service import load_assets_for_evaluation

            async with SessionLocal() as session:
                engineer = await load_user_context(session, engineer_id)
                outsider = await load_user_context(session, outsider_id)
                assert engineer is not None and outsider is not None
                engineer_assets = await load_assets_for_evaluation(
                    session, tenant_id, company_id, user=engineer
                )
                outsider_assets = await load_assets_for_evaluation(
                    session, tenant_id, company_id, user=outsider
                )
            engineer_names = {row["name"] for row in engineer_assets["qualifications"]}
            outsider_names = {row["name"] for row in outsider_assets["qualifications"]}
            assert "部门专属资质" in engineer_names
            assert "部门专属资质" not in outsider_names
            assert "企业共享资质" in outsider_names
    finally:
        await cleanup_tenants([tenant_id])


@pytest.mark.integration
async def test_knowledge_upload_rejects_cross_tenant_company() -> None:
    tenant_a, _, admin_a_email = await seed_tenant_user("review-company-a")
    tenant_b, _, admin_b_email = await seed_tenant_user("review-company-b")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            admin_a = await login(client, admin_a_email)
            admin_b = await login(client, admin_b_email)
            company_b = await client.post(
                "/api/v1/companies", json={"name": "B 租户企业"}, headers=admin_b
            )
            assert company_b.status_code == 201, company_b.text
            bases = await client.get("/api/v1/knowledge/bases", headers=admin_a)
            default_kb = next(
                row for row in bases.json() if row["name"] == "企业知识库"
            )
            response = await client.post(
                "/api/v1/knowledge/documents",
                files={
                    "file": (
                        "cross.docx",
                        sample_tender_docx(),
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    )
                },
                data={
                    "kb_id": default_kb["id"],
                    "company_id": company_b.json()["id"],
                },
                headers=admin_a,
            )
            assert response.status_code == 404, response.text
    finally:
        await cleanup_tenants([tenant_a, tenant_b])


@pytest.mark.integration
async def test_export_endpoints_require_export_capability() -> None:
    tenant_id, _, admin_email = await seed_tenant_user("review-export-admin")
    _, _, engineer_email = await seed_tenant_user(
        "review-export-engineer", "bid_engineer", tenant_id
    )
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            engineer = await login(client, engineer_email)
            project_id = "00000000-0000-0000-0000-000000000001"
            responses = await client.get(
                f"/api/v1/projects/{project_id}/responses/export", headers=engineer
            )
            assert responses.status_code == 403, responses.text
            contract = await client.get(
                f"/api/v1/projects/{project_id}/contract-reviews/"
                "00000000-0000-0000-0000-000000000002/export",
                headers=engineer,
            )
            assert contract.status_code == 403, contract.text
            report = await client.post(
                "/api/v1/compliance/reports/"
                "00000000-0000-0000-0000-000000000003/export",
                json={"format": "xlsx"},
                headers=engineer,
            )
            assert report.status_code == 403, report.text
    finally:
        await cleanup_tenants([tenant_id])


@pytest.mark.integration
async def test_reenable_user_restores_orphaned_personal_kb() -> None:
    tenant_id, admin_id, admin_email = await seed_tenant_user("review-offboard-admin")
    _, engineer_id, engineer_email = await seed_tenant_user(
        "review-offboard-engineer", "bid_engineer", tenant_id
    )
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            admin = await login(client, admin_email)
            engineer = await login(client, engineer_email)
            created = await client.post(
                "/api/v1/knowledge/bases",
                json={"name": "离职交接个人库", "scope": "personal"},
                headers=engineer,
            )
            assert created.status_code == 201, created.text
            personal_kb_id = created.json()["id"]

            disabled = await client.patch(
                f"/api/v1/users/{engineer_id}", json={"status": "disabled"}, headers=admin
            )
            assert disabled.status_code == 200, disabled.text
            enabled = await client.patch(
                f"/api/v1/users/{engineer_id}", json={"status": "active"}, headers=admin
            )
            assert enabled.status_code == 200, enabled.text

            engineer = await login(client, engineer_email)
            bases = await client.get("/api/v1/knowledge/bases", headers=engineer)
            assert bases.status_code == 200
            assert personal_kb_id in {row["id"] for row in bases.json()}
    finally:
        await cleanup_tenants([tenant_id])
