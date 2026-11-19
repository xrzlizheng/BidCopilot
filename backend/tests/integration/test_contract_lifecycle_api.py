"""合同全生命周期 API 集成测试（需 PostgreSQL；本机无 PG，未执行）。

覆盖：合同主数据 -> 起草来源 -> 审批状态机（非法跳转 409）-> 履约台账
Decimal 汇总 -> 变更/续签留痕 -> xlsx 导出 -> 跨租户 404。
"""

from io import BytesIO

import pytest
from httpx import ASGITransport, AsyncClient
from openpyxl import load_workbook

from app.main import app
from tests.support import cleanup_tenants, login, seed_tenant_user


async def _create_project(client: AsyncClient, headers: dict[str, str], name: str) -> str:
    response = await client.post("/api/v1/projects", json={"name": name}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()["id"]


@pytest.mark.integration
async def test_contract_lifecycle_full_flow() -> None:
    tenant_id, _, email = await seed_tenant_user("clm-a")
    other_tenant_id, _, other_email = await seed_tenant_user("clm-b")
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = await login(client, email)
            project_id = await _create_project(client, headers, "CLM 合同项目")

            created = await client.post(
                "/api/v1/contract-records",
                json={
                    "project_id": project_id,
                    "contract_no": "HT-CLM-001",
                    "title": "某政务云运维合同",
                    "party_a": "某市大数据局",
                    "party_b": "投标人科技有限公司",
                    "effective_date": "2026-11-01",
                    "expiry_date": "2027-10-31",
                    "amount": "1000000.00",
                    "currency": "CNY",
                },
                headers=headers,
            )
            assert created.status_code == 201, created.text
            record = created.json()
            record_id = record["id"]
            assert record["amount"] == "1000000.00"
            assert record["status"] == "draft"

            # 非法跳转：draft 不能直接 approve
            illegal = await client.post(
                f"/api/v1/contract-records/{record_id}/approve",
                json={"comment": "试图跳过提交"},
                headers=headers,
            )
            assert illegal.status_code == 409, illegal.text
            assert illegal.json()["code"] == "invalid_status_transition"

            # 起草：每条条款必须有来源，整稿有免责声明
            drafted = await client.post(
                f"/api/v1/contract-records/{record_id}/draft", headers=headers
            )
            assert drafted.status_code == 200, drafted.text
            draft_body = drafted.json()
            assert draft_body["disclaimer"].startswith("机器生成草稿，需法务复核")
            assert draft_body["generated_count"] >= 5
            for clause in draft_body["record"]["draft"]:
                assert clause["source_json"]
                assert clause["source_json"]["type"] in {
                    "bid_commitment",
                    "clause_template",
                    "contract_review_clause",
                }

            submitted = await client.post(
                f"/api/v1/contract-records/{record_id}/submit",
                json={"comment": "提交法务审批"},
                headers=headers,
            )
            assert submitted.status_code == 200, submitted.text
            assert submitted.json()["status"] == "pending_approval"

            approved = await client.post(
                f"/api/v1/contract-records/{record_id}/approve",
                json={"comment": "法务通过"},
                headers=headers,
            )
            assert approved.status_code == 200, approved.text
            assert approved.json()["status"] == "active"

            repeat_approve = await client.post(
                f"/api/v1/contract-records/{record_id}/approve",
                json={"comment": "重复审批"},
                headers=headers,
            )
            assert repeat_approve.status_code == 409
            assert repeat_approve.json()["code"] == "invalid_status_transition"

            approvals = await client.get(
                f"/api/v1/contract-records/{record_id}/approvals", headers=headers
            )
            assert approvals.status_code == 200
            actions = [row["action"] for row in approvals.json()]
            assert actions == ["submit", "approve"]

            # 履约台账：0.10 + 0.20 必须精确等于 0.30
            first_invoice = await client.post(
                f"/api/v1/contract-records/{record_id}/ledger",
                json={
                    "entry_type": "invoice",
                    "amount": "0.10",
                    "occurred_on": "2026-11-02",
                    "note": "首期开票",
                    "voucher_ref": "FP-001",
                },
                headers=headers,
            )
            assert first_invoice.status_code == 201, first_invoice.text
            assert first_invoice.json()["amount"] == "0.10"
            second_invoice = await client.post(
                f"/api/v1/contract-records/{record_id}/ledger",
                json={
                    "entry_type": "invoice",
                    "amount": "0.20",
                    "occurred_on": "2026-11-03",
                    "note": "二期开票",
                    "voucher_ref": "FP-002",
                },
                headers=headers,
            )
            assert second_invoice.status_code == 201, second_invoice.text
            payment = await client.post(
                f"/api/v1/contract-records/{record_id}/ledger",
                json={
                    "entry_type": "payment",
                    "amount": "0.10",
                    "occurred_on": "2026-11-05",
                    "note": "首期回款",
                    "voucher_ref": "SK-001",
                },
                headers=headers,
            )
            assert payment.status_code == 201, payment.text

            ledger = await client.get(
                f"/api/v1/contract-records/{record_id}/ledger", headers=headers
            )
            assert ledger.status_code == 200, ledger.text
            summary = ledger.json()["summary"]
            assert summary["invoice_total"] == "0.30"
            assert summary["payment_total"] == "0.10"
            assert summary["outstanding"] == "0.20"
            assert len(ledger.json()["entries"]) == 3

            # 变更必须留痕：审批通过后主数据更新，但历史记录仍保留变更前后值
            change = await client.post(
                f"/api/v1/contract-records/{record_id}/changes",
                json={
                    "change_type": "change",
                    "amount_after": "1200000.50",
                    "reason": "增加运维范围",
                },
                headers=headers,
            )
            assert change.status_code == 201, change.text
            change_id = change.json()["id"]
            assert change.json()["approval_status"] == "pending"
            assert change.json()["amount_before"] == "1000000.00"

            approved_change = await client.post(
                f"/api/v1/contract-records/{record_id}/changes/{change_id}/approve",
                headers=headers,
            )
            assert approved_change.status_code == 200, approved_change.text
            assert approved_change.json()["approval_status"] == "approved"

            detail = await client.get(
                f"/api/v1/contract-records/{record_id}", headers=headers
            )
            assert detail.json()["amount"] == "1200000.50"
            changes = await client.get(
                f"/api/v1/contract-records/{record_id}/changes", headers=headers
            )
            assert changes.status_code == 200
            rows = changes.json()["items"]
            assert len(rows) == 1
            assert rows[0]["amount_before"] == "1000000.00"
            assert rows[0]["amount_after"] == "1200000.50"
            assert rows[0]["reason"] == "增加运维范围"

            # 已审批变更不可再次决策
            repeat_change = await client.post(
                f"/api/v1/contract-records/{record_id}/changes/{change_id}/reject",
                headers=headers,
            )
            assert repeat_change.status_code == 409
            assert repeat_change.json()["code"] == "change_already_decided"

            # 导出 xlsx：表头 + 合计行；备注不成为公式
            exported = await client.get(
                f"/api/v1/contract-records/{record_id}/export", headers=headers
            )
            assert exported.status_code == 200, exported.text
            workbook = load_workbook(BytesIO(exported.content))
            sheet = workbook["履约台账"]
            assert sheet["A1"].value == "序号"
            labels = [
                sheet.cell(row=row, column=1).value
                for row in range(1, sheet.max_row + 1)
            ]
            assert "合计" in labels
            assert "未收余额" in labels
            workbook.close()

            # 跨租户：404，不泄露存在性
            other_headers = await login(client, other_email)
            forbidden_get = await client.get(
                f"/api/v1/contract-records/{record_id}", headers=other_headers
            )
            assert forbidden_get.status_code == 404
            forbidden_ledger = await client.post(
                f"/api/v1/contract-records/{record_id}/ledger",
                json={
                    "entry_type": "payment",
                    "amount": "1.00",
                    "occurred_on": "2026-11-06",
                },
                headers=other_headers,
            )
            assert forbidden_ledger.status_code == 404
            forbidden_patch = await client.patch(
                f"/api/v1/contract-records/{record_id}",
                json={"title": "越权改名"},
                headers=other_headers,
            )
            assert forbidden_patch.status_code == 404
    finally:
        app.dependency_overrides.clear()
        await cleanup_tenants([tenant_id, other_tenant_id])
