from decimal import Decimal

from app.modules.pricing.cost_model import LaborLine, build_cost_baseline, parse_cost_model
from app.modules.pricing.engine import (
    DEFAULT_TARGET_MARGINS,
    build_sensitivity,
    build_strategy_payload,
    build_tiers,
    template_narrative,
)
from app.modules.pricing.price_band import build_price_band

D = Decimal

RAW_MODEL = {
    "direct_rate": "0.55",
    "indirect_rate": "0.12",
    "tax_rate": "0.06",
    "labor_rates": {"工程师": "1200"},
}


def _cost(quote: D | None = D("50000")):
    model = parse_cost_model(RAW_MODEL, source="tenant-setting:pricing_cost_model")
    return build_cost_baseline(
        model,
        labor_items=[LaborLine(role="工程师", days=D("20"))],
        other_direct_cost=D("5000"),
        quote=quote,
    )


def _band(samples: int = 5):
    ratios = [D("0.80"), D("0.85"), D("0.88"), D("0.90"), D("0.95")][:samples]
    return build_price_band(
        award_ratios=ratios,
        decided_outcomes=["won"] * 6 + ["lost"] * 3,
        price_loss_count=2,
    )


def test_sensitivity_has_seven_points_and_varies_margin_and_win_rate() -> None:
    result = build_sensitivity(
        base_quote=D("50000"),
        base_source="input",
        budget=D("52000"),
        direct_cost=D("29000"),
        indirect_rate=D("0.12"),
        tax_rate=D("0.06"),
        band=_band(),
    )

    assert result["status"] == "ok"
    assert len(result["rows"]) == 7
    changes = [Decimal(row["change_rate"]) for row in result["rows"]]
    assert D("-0.08") in changes and D("-0.05") in changes and D("-0.03") in changes
    assert D("0.03") in changes and D("0.05") in changes and D("0.08") in changes
    margins = [Decimal(row["net_margin"]) for row in result["rows"]]
    win_rates = [Decimal(row["win_rate"]) for row in result["rows"]]
    assert margins == sorted(margins)
    assert win_rates == sorted(win_rates, reverse=True)
    assert len(set(win_rates)) > 1


def test_sensitivity_insufficient_without_cost_parameters() -> None:
    result = build_sensitivity(
        base_quote=D("50000"),
        base_source="input",
        budget=D("52000"),
        direct_cost=None,
        indirect_rate=None,
        tax_rate=None,
        band=_band(),
    )
    assert result["status"] == "insufficient_data"
    assert result["rows"] == []
    assert result["missing"]


def test_three_tiers_are_market_anchored_when_band_available() -> None:
    tiers = build_tiers(
        direct_cost=D("29000"),
        indirect_rate=D("0.12"),
        tax_rate=D("0.06"),
        budget=D("52000"),
        band=_band(),
        target_margins=dict(DEFAULT_TARGET_MARGINS),
    )
    assert [tier["tier"] for tier in tiers] == ["conservative", "balanced", "aggressive"]
    prices = [Decimal(tier["price"]) for tier in tiers]
    assert prices[0] > prices[1] > prices[2]
    assert all(tier["reason"] for tier in tiers)
    assert all(tier["risks"] for tier in tiers)
    # 价格带可用时按历史 P75/P50/P25 锚定（0.90/0.88/0.85 x 52000）
    assert all(tier["price_source"] == "market_band" for tier in tiers)
    assert prices == [D("46800.00"), D("45760.00"), D("44200.00")]
    # 成本加成价仍作为对照字段保留
    assert Decimal(tiers[1]["cost_plus_price"]) == D("39189.19")
    # 目标毛利率照常披露，但净利率由锚定价真实反算，不再等于目标值
    assert Decimal(tiers[1]["target_margin"]) == D("0.08")
    assert Decimal(tiers[1]["net_margin"]) != D("0.08")


def test_three_tiers_fall_back_to_cost_plus_when_band_insufficient() -> None:
    tiers = build_tiers(
        direct_cost=D("29000"),
        indirect_rate=D("0.12"),
        tax_rate=D("0.06"),
        budget=D("52000"),
        band=_band(samples=4),
        target_margins=dict(DEFAULT_TARGET_MARGINS),
    )
    assert all(tier["price_source"] == "cost_plus" for tier in tiers)
    assert Decimal(tiers[1]["net_margin"]) == D("0.08")


def test_three_tiers_clamp_to_cost_floor_and_budget_cap() -> None:
    tiers = build_tiers(
        direct_cost=D("50000"),
        indirect_rate=D("0.12"),
        tax_rate=D("0.06"),
        budget=D("52000"),
        band=_band(),
        target_margins=dict(DEFAULT_TARGET_MARGINS),
    )
    floor = Decimal("60975.61")
    for tier in tiers:
        assert tier["clamp_notes"]
        assert Decimal(tier["cost_floor_price"]) == floor
        # 先按成本底线上调，再受预算封顶，最终不得高于预算
        assert Decimal(tier["price"]) == D("52000.00")
        assert tier["price_source"] == "market_band_budget_cap"
        # 触顶后低于成本，必须显式告警
        assert Decimal(tier["net_margin"]) < 0
        assert any("净利率为负" in risk for risk in tier["risks"])

def test_strategy_payload_marks_insufficient_when_band_samples_low() -> None:
    payload = build_strategy_payload(
        budget=D("52000"),
        budget_source="input",
        industry=["软件和信息技术服务业"],
        industry_source="input",
        quote=D("50000"),
        cost=_cost(),
        band=_band(samples=4),
        indirect_rate=D("0.12"),
        tax_rate=D("0.06"),
        target_margins=dict(DEFAULT_TARGET_MARGINS),
        target_margins_source="tool-default",
    )
    assert payload["status"] == "insufficient_data"
    assert payload["price_band"]["status"] == "insufficient_data"
    assert any("样本不足" in item for item in payload["missing"])
    assert payload["candidate"]["net_margin"] == "0.240000"
    assert "非模型预测" in payload["disclaimer"]


def test_strategy_payload_ok_contains_tiers_sensitivity_and_candidate() -> None:
    payload = build_strategy_payload(
        budget=D("52000"),
        budget_source="input",
        industry=["软件和信息技术服务业"],
        industry_source="input",
        quote=D("50000"),
        cost=_cost(),
        band=_band(),
        indirect_rate=D("0.12"),
        tax_rate=D("0.06"),
        target_margins=dict(DEFAULT_TARGET_MARGINS),
        target_margins_source="tool-default",
    )
    assert payload["status"] == "ok"
    assert payload["missing"] == []
    assert len(payload["tiers"]) == 3
    assert len(payload["sensitivity"]["rows"]) == 7
    assert payload["candidate"]["quote_amount"] == "50000.00"
    assert payload["candidate"]["win_rate"]["heuristic"] is True
    assert "非中标承诺" in payload["candidate"]["win_rate"]["disclaimer"]


def test_strategy_payload_missing_budget_is_insufficient() -> None:
    payload = build_strategy_payload(
        budget=None,
        budget_source="missing",
        industry=["软件和信息技术服务业"],
        industry_source="input",
        quote=D("50000"),
        cost=_cost(),
        band=_band(),
        indirect_rate=D("0.12"),
        tax_rate=D("0.06"),
        target_margins=dict(DEFAULT_TARGET_MARGINS),
        target_margins_source="tool-default",
    )
    assert payload["status"] == "insufficient_data"
    assert any("预算" in item for item in payload["missing"])


def test_template_narrative_states_heuristic_not_commitment() -> None:
    payload = build_strategy_payload(
        budget=D("52000"),
        budget_source="input",
        industry=["软件和信息技术服务业"],
        industry_source="input",
        quote=D("50000"),
        cost=_cost(),
        band=_band(),
        indirect_rate=D("0.12"),
        tax_rate=D("0.06"),
        target_margins=dict(DEFAULT_TARGET_MARGINS),
        target_margins_source="tool-default",
    )
    text = template_narrative(payload)
    assert "启发式" in text
    assert "不是模型预测" in text
    assert "中标承诺" in text
    assert "P50" in text


def test_template_narrative_for_insufficient_data_lists_missing_items() -> None:
    payload = build_strategy_payload(
        budget=None,
        budget_source="missing",
        industry=[],
        industry_source="missing",
        quote=None,
        cost=_cost(quote=None),
        band=_band(samples=2),
        indirect_rate=D("0.12"),
        tax_rate=D("0.06"),
        target_margins=dict(DEFAULT_TARGET_MARGINS),
        target_margins_source="tool-default",
    )
    text = template_narrative(payload)
    assert payload["status"] == "insufficient_data"
    assert "数据不足" in text
    assert "未使用默认值" in text

def test_strategy_payload_insufficient_when_decided_samples_low() -> None:
    band = build_price_band(
        award_ratios=[D("0.80"), D("0.85"), D("0.88"), D("0.90"), D("0.95")],
        decided_outcomes=["won", "lost"],
        price_loss_count=1,
    )
    payload = build_strategy_payload(
        budget=D("52000"),
        budget_source="input",
        industry=["软件和信息技术服务业"],
        industry_source="input",
        quote=D("50000"),
        cost=_cost(),
        band=band,
        indirect_rate=D("0.12"),
        tax_rate=D("0.06"),
        target_margins=dict(DEFAULT_TARGET_MARGINS),
        target_margins_source="tool-default",
    )
    assert payload["status"] == "insufficient_data"
    assert any("已决标样本不足" in item for item in payload["missing"])
