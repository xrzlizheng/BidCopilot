from decimal import Decimal

import pytest

from app.modules.pricing.cost_model import (
    LaborLine,
    build_cost_baseline,
    net_margin_at_quote,
    parse_cost_model,
    solve_quote_for_margin,
)

D = Decimal

RAW_MODEL = {
    "direct_rate": "0.55",
    "indirect_rate": "0.12",
    "tax_rate": "0.06",
    "labor_rates": {"工程师": "1200", "项目经理": "1800"},
}


def test_cost_baseline_decomposes_labor_overhead_tax() -> None:
    model = parse_cost_model(RAW_MODEL, source="tenant-setting:pricing_cost_model")
    cost = build_cost_baseline(
        model,
        labor_items=[LaborLine(role="工程师", days=D("20"))],
        other_direct_cost=D("5000"),
        quote=D("50000"),
    )

    assert cost.status == "ok"
    assert cost.missing == []
    assert cost.labor_cost == D("24000")
    assert cost.direct_cost == D("29000")
    assert cost.indirect_cost == D("6000.00")
    assert cost.tax_cost == D("3000.00")
    assert cost.total_cost == D("38000.00")
    assert cost.net_profit == D("12000.00")
    assert cost.net_margin == D("0.24")
    # direct_rate 是租户校准基准：本单直接成本率 58%，比校准值高 3 个百分点。
    assert cost.direct_cost_ratio == D("0.58")
    assert cost.direct_rate_variance == D("0.03")
    assert cost.source_labels["rates"] == "tenant-setting:pricing_cost_model"


def test_cost_baseline_missing_rate_is_insufficient_not_defaulted() -> None:
    raw = dict(RAW_MODEL)
    raw.pop("direct_rate")
    model = parse_cost_model(raw, source="tenant-setting:pricing_cost_model")
    cost = build_cost_baseline(
        model,
        labor_items=[LaborLine(role="工程师", days=D("20"))],
        other_direct_cost=D("5000"),
        quote=D("50000"),
    )

    assert cost.status == "insufficient_data"
    assert any("direct_rate" in item for item in cost.missing)
    # 间接费/税仍可算出，但缺直接成本率时整体状态不得标 ok。
    assert cost.net_margin is not None


def test_unknown_labor_role_returns_missing_not_zero_cost() -> None:
    model = parse_cost_model(RAW_MODEL, source="tenant-setting:pricing_cost_model")
    cost = build_cost_baseline(
        model,
        labor_items=[LaborLine(role="未配置岗位", days=D("10"))],
        other_direct_cost=D("0"),
        quote=D("50000"),
    )

    assert cost.status == "insufficient_data"
    assert cost.direct_cost is None
    assert any("未配置岗位" in item for item in cost.missing)


def test_missing_other_direct_cost_is_explicit_not_assumed_zero() -> None:
    model = parse_cost_model(RAW_MODEL, source="tenant-setting:pricing_cost_model")
    cost = build_cost_baseline(
        model,
        labor_items=[LaborLine(role="工程师", days=D("1"))],
        other_direct_cost=None,
        quote=D("50000"),
    )

    assert cost.status == "insufficient_data"
    assert cost.other_direct_cost is None
    assert any("其他直接成本" in item for item in cost.missing)


def test_solve_quote_for_target_margin_round_trip() -> None:
    quote, error = solve_quote_for_margin(
        direct_cost=D("29000"),
        indirect_rate=D("0.12"),
        tax_rate=D("0.06"),
        target_margin=D("0.08"),
    )

    assert error is None
    assert quote == D("39189.19")
    margin = net_margin_at_quote(
        quote=quote,
        direct_cost=D("29000"),
        indirect_rate=D("0.12"),
        tax_rate=D("0.06"),
    )
    assert margin == pytest.approx(D("0.08"), abs=D("0.000001"))


def test_invalid_rate_is_rejected_instead_of_used() -> None:
    raw = dict(RAW_MODEL)
    raw["tax_rate"] = "1.5"
    model = parse_cost_model(raw, source="tenant-setting:pricing_cost_model")
    assert model.tax_rate is None
    assert any("tax_rate" in item for item in model.missing)


def test_labor_rates_accept_list_form() -> None:
    model = parse_cost_model(
        {
            "direct_rate": "0.5",
            "indirect_rate": "0.1",
            "tax_rate": "0.06",
            "labor_rates": [{"role": "架构师", "daily_rate": "2500"}],
        },
        source="tenant-setting:pricing_cost_model",
    )
    assert model.labor_rates == {"架构师": D("2500")}
