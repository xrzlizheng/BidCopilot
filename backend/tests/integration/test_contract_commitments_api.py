import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from tests.support import cleanup_tenants, login, seed_tenant_user

TENDER = """
某市政务云运维服务项目招标文件
项目预算：人民币 150 万元。
投标截止时间：2027-01-08 10:00。
投标人须具备信息系统集成资质。
"""

CONTRACT = """
合同总价：人民币 900000 元。
工期：120 天。
质保期：1 年。
付款方式：验收后 30 日内支付。
"""

# 与 CONTRACT 内容不同（file_hash 不同），用于第二次审查；
# contract_reviews 对 (tenant_id, project_id, file_hash) 有唯一约束。
CONTRACT_ZERO = """
合同总价：人民币 950000 元。
工期：120 天。
质保期：1 年。
验收标准：按招标文件执行。
"""


@pytest.mark.integration
async def test_contract_compare_against_bid_commitments() -> None:
    tenant_id, _, email = await seed_tenant_user("contract-commitment")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = await login(client, email)
            project = await client.post(
                "/api/v1/projects", json={"name": "承诺衔接项目"}, headers=headers
            )
            project_id = project.json()["id"]
            uploaded = await client.post(
                f"/api/v1/projects/{project_id}/documents",
                files={"file": ("tender.txt", TENDER.encode(), "text/plain")},
                headers=headers,
            )
            assert uploaded.status_code == 200, uploaded.text

            snapshot = await client.post(
                f"/api/v1/projects/{project_id}/commitments/snapshot",
                json={
                    "quote_amount": 1000000,
                    "duration_days": 90,
                    "warranty_days": 365,
                    "personnel": ["张三"],
                },
                headers=headers,
            )
            assert snapshot.status_code == 200, snapshot.text
            kinds = {row["kind"] for row in snapshot.json()}
            assert {"price", "schedule", "warranty", "personnel"} <= kinds

            listed = await client.get(
                f"/api/v1/projects/{project_id}/commitments", headers=headers
            )
            assert listed.status_code == 200, listed.text
            assert len(listed.json()) >= 4

            review = await client.post(
                f"/api/v1/projects/{project_id}/contract-reviews",
                files={"file": ("contract.txt", CONTRACT.encode(), "text/plain")},
                headers=headers,
            )
            assert review.status_code == 200, review.text
            rule_ids = {finding["rule_id"] for finding in review.json()["findings"]}
            assert "commitment_price_mismatch" in rule_ids
            assert "commitment_schedule_mismatch" in rule_ids
            assert "commitment_personnel_absent" in rule_ids

            # 重复上传同一份合同：唯一约束命中时应返回 409，而不是 503
            duplicate = await client.post(
                f"/api/v1/projects/{project_id}/contract-reviews",
                files={"file": ("contract.txt", CONTRACT.encode(), "text/plain")},
                headers=headers,
            )
            assert duplicate.status_code == 409, duplicate.text
            assert duplicate.json()["code"] == "conflict"

            # [P1-1] 验证投标报价为 0 时的除零边界安全
            await client.post(
                f"/api/v1/projects/{project_id}/commitments/snapshot",
                json={"quote_amount": 0},
                headers=headers,
            )
            review_zero = await client.post(
                f"/api/v1/projects/{project_id}/contract-reviews",
                files={"file": ("contract-zero.txt", CONTRACT_ZERO.encode(), "text/plain")},
                headers=headers,
            )
            assert review_zero.status_code == 200, review_zero.text
            zero_rule_ids = {finding["rule_id"] for finding in review_zero.json()["findings"]}
            # 0 元报价不参与偏离比对，且不得因除零中断审查
            assert "commitment_price_mismatch" not in zero_rule_ids
    finally:
        await cleanup_tenants([tenant_id])
