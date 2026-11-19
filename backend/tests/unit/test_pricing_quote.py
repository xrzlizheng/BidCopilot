from datetime import UTC, datetime
from decimal import Decimal
from io import BytesIO
from zipfile import ZipFile

import pytest

from app.core.errors import ValidationAppError
from app.modules.pricing.models import PriceQuote
from app.modules.pricing.quote import (
    build_quote_document,
    build_quote_items,
    resolve_quote_total,
    rmb_upper,
)
from app.modules.pricing.schemas import QuoteItemInput

D = Decimal


@pytest.mark.parametrize(
    ("amount", "expected"),
    [
        (D("0"), "零元整"),
        (D("0.50"), "零元伍角整"),
        (D("0.05"), "零元伍分"),
        (D("10.10"), "壹拾元壹角整"),
        (D("100.05"), "壹佰元零伍分"),
        (D("1005"), "壹仟零伍元整"),
        (D("1010"), "壹仟零壹拾元整"),
        (D("100000000"), "壹亿元整"),
        (D("100000001.01"), "壹亿零壹元零壹分"),
        (D("100001000"), "壹亿零壹仟元整"),
        (D("100010000"), "壹亿零壹万元整"),
        (D("39189.19"), "叁万玖仟壹佰捌拾玖元壹角玖分"),
        (D("1000000000000"), "壹兆元整"),
        (D("9999999999999999.99"), "玖仟玖佰玖拾玖兆玖仟玖佰玖拾玖亿玖仟玖佰玖拾玖万玖仟玖佰玖拾玖元玖角玖分"),
    ],
)
def test_rmb_upper(amount: Decimal, expected: str) -> None:
    assert rmb_upper(amount) == expected


def test_rmb_upper_rejects_negative() -> None:
    with pytest.raises(ValueError):
        rmb_upper(D("-1"))


def _strategy_result(tiers: list[dict]) -> dict:
    return {"tiers": tiers}


def test_resolve_total_prefers_explicit_override() -> None:
    total, source = resolve_quote_total(_strategy_result([]), None, D("50000"))
    assert total == D("50000.00")
    assert source == "input"


def test_resolve_total_defaults_to_balanced_tier() -> None:
    result = _strategy_result(
        [{"tier": "balanced", "status": "ok", "price": "39189.19"}]
    )
    total, source = resolve_quote_total(result, None, None)
    assert total == D("39189.19")
    assert source == "tier:balanced"


def test_resolve_total_selected_tier() -> None:
    result = _strategy_result(
        [
            {"tier": "balanced", "status": "ok", "price": "39189.19"},
            {"tier": "aggressive", "status": "ok", "price": "37179.49"},
        ]
    )
    total, source = resolve_quote_total(result, "aggressive", None)
    assert total == D("37179.49")
    assert source == "tier:aggressive"


def test_resolve_total_missing_tier_raises() -> None:
    with pytest.raises(ValidationAppError, match="不可用"):
        resolve_quote_total(_strategy_result([]), "conservative", None)


def test_resolve_total_unexecutable_tier_raises() -> None:
    result = _strategy_result(
        [
            {
                "tier": "aggressive",
                "status": "insufficient_data",
                "price": "30000.00",
                "risks": ["招标预算低于成本底线，该档报价不可执行"],
            }
        ]
    )
    with pytest.raises(ValidationAppError, match="不可执行"):
        resolve_quote_total(result, "aggressive", None)


def test_build_items_defaults_to_single_total_line() -> None:
    rows = build_quote_items([], D("50000.00"), "某项目实施服务")
    assert rows == [
        {
            "name": "某项目实施服务",
            "spec": "",
            "quantity": "1",
            "unit": "项",
            "unit_price": "50000.00",
            "amount": "50000.00",
        }
    ]


def test_build_items_accepts_matching_subtotal() -> None:
    items = [
        QuoteItemInput(name="软件开发", quantity=D("1"), unit="项", unit_price=D("30000")),
        QuoteItemInput(name="实施运维", spec="一年", quantity=D("2"), unit="年", unit_price=D("10000")),
    ]
    rows = build_quote_items(items, D("50000.00"), "默认")
    assert [row["amount"] for row in rows] == ["30000.00", "20000.00"]
    assert rows[1]["spec"] == "一年"


def test_build_items_rejects_mismatched_subtotal() -> None:
    items = [
        QuoteItemInput(name="软件开发", quantity=D("1"), unit="项", unit_price=D("30000")),
    ]
    with pytest.raises(ValidationAppError, match="不一致"):
        build_quote_items(items, D("50000.00"), "默认")


def test_quote_document_renders_key_fields() -> None:
    row = PriceQuote(
        quote_number="BJ-20261117-ABCD1234",
        project_name="某市政信息化项目",
        buyer_name="某市大数据局",
        supplier_name="测试科技有限公司",
        total_amount=D("50000.00"),
        currency="CNY",
        amount_in_words=rmb_upper(D("50000.00")),
        items_json=[
            {
                "name": "软件开发",
                "spec": "含部署",
                "quantity": "1",
                "unit": "项",
                "unit_price": "50000.00",
                "amount": "50000.00",
            }
        ],
        tier="balanced",
        price_source="tier:balanced",
        valid_until=datetime(2027, 2, 15, tzinfo=UTC),
        notes="含税总价",
        created_at=datetime(2026, 11, 17, tzinfo=UTC),
    )
    document = build_quote_document(row, "ZFCG-2026-001")
    buffer = BytesIO()
    document.save(buffer)
    with ZipFile(BytesIO(buffer.getvalue())) as archive:
        xml = archive.read("word/document.xml").decode("utf-8")
    for expected in (
        "报 价 单",
        "某市政信息化项目",
        "ZFCG-2026-001",
        "某市大数据局",
        "测试科技有限公司",
        "BJ-20261117-ABCD1234",
        "软件开发",
        "伍万元整",
        "含税总价",
        "法定代表人或授权代表（签字）",
    ):
        assert expected in xml