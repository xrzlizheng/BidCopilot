from decimal import Decimal
from uuid import uuid4

from app.modules.projects.commitments import compare_contract
from app.modules.projects.models import BidCommitment


def test_compare_contract_zero_or_negative_commitment_price() -> None:
    """[P1-1] 验证当投标承诺金额为 0 或负数时，不会发生 ZeroDivisionError。"""
    clauses = [{"category": "price", "text": "合同总价：人民币 100000 元。", "seq": 1}]
    zero_commitment = BidCommitment(
        id=uuid4(),
        tenant_id=uuid4(),
        project_id=uuid4(),
        kind="price",
        label="投标报价",
        value_text="0 元",
        value_amount=Decimal("0"),
        source="manual",
    )
    # 不应抛出 ZeroDivisionError
    findings = compare_contract(clauses, [zero_commitment])
    assert findings == []

    negative_commitment = BidCommitment(
        id=uuid4(),
        tenant_id=uuid4(),
        project_id=uuid4(),
        kind="price",
        label="投标报价",
        value_text="-100 元",
        value_amount=Decimal("-100"),
        source="manual",
    )
    findings = compare_contract(clauses, [negative_commitment])
    assert findings == []


def test_compare_contract_normal_mismatch() -> None:
    """验证正常价格偏离仍能正确检出。"""
    clauses = [{"category": "price", "text": "合同总价：人民币 900000 元。", "seq": 1}]
    commitment = BidCommitment(
        id=uuid4(),
        tenant_id=uuid4(),
        project_id=uuid4(),
        kind="price",
        label="投标报价",
        value_text="1000000 元",
        value_amount=Decimal("1000000"),
        source="manual",
    )
    findings = compare_contract(clauses, [commitment])
    assert len(findings) == 1
    assert findings[0]["rule_id"] == "commitment_price_mismatch"
    assert "偏离 10.0%" in findings[0]["message"]


def test_tenant_admin_role_includes_tenant_settings_manage() -> None:
    """验证企业管理员包含 tenant:settings:manage 权限。"""
    from app.modules.identity.roles import SYSTEM_ROLES

    assert "tenant:settings:manage" in SYSTEM_ROLES["tenant_admin"]["permissions"]
