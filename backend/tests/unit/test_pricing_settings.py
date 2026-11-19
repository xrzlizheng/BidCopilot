from decimal import Decimal

import pytest

from app.modules.pricing.cost_model import parse_cost_model
from app.modules.system.pricing_settings import (
    build_setting_updates,
    validate_cost_model_block,
    validate_target_margins_block,
)
from app.modules.system.schemas import PricingSettingsUpdate

D = Decimal

VALID_MODEL = {
    "direct_rate": "0.55",
    "indirect_rate": "0.12",
    "tax_rate": "0.06",
    "labor_rates": {"工程师": "1200"},
}
VALID_MARGINS = {"conservative": "0.12", "balanced": "0.08", "aggressive": "0.04"}


def test_valid_cost_model_normalizes_to_strings_and_round_trips_engine() -> None:
    stored = validate_cost_model_block(
        {**VALID_MODEL, "labor_rates": {" 工程师 ": "1200"}}
    )
    assert stored == {
        "direct_rate": "0.550000",
        "indirect_rate": "0.120000",
        "tax_rate": "0.060000",
        "labor_rates": {"工程师": "1200.00"},
    }
    # 存储值必须能被引擎口径原样消费，不产生缺项。
    model = parse_cost_model(stored, source="tenant-setting:pricing_cost_model")
    assert model.missing == ()
    assert model.direct_rate == D("0.55")
    assert model.labor_rates == {"工程师": D("1200.00")}


def test_null_or_empty_rates_are_allowed_as_unset() -> None:
    stored = validate_cost_model_block(
        {"direct_rate": None, "indirect_rate": "", "tax_rate": "0.06"}
    )
    assert stored["direct_rate"] is None
    assert stored["indirect_rate"] is None
    assert stored["tax_rate"] == "0.060000"


@pytest.mark.parametrize("key", ["direct_rate", "indirect_rate", "tax_rate"])
@pytest.mark.parametrize("bad", ["-0.01", "1", "1.5", "abc", "NaN"])
def test_invalid_rates_rejected(key: str, bad: str) -> None:
    with pytest.raises(ValueError):
        validate_cost_model_block({**VALID_MODEL, key: bad})


def test_indirect_plus_tax_must_be_below_one() -> None:
    with pytest.raises(ValueError):
        validate_cost_model_block(
            {**VALID_MODEL, "indirect_rate": "0.6", "tax_rate": "0.4"}
        )
    # 边界内允许：0.94 + 0.05 < 1
    stored = validate_cost_model_block(
        {**VALID_MODEL, "indirect_rate": "0.94", "tax_rate": "0.05"}
    )
    assert stored["indirect_rate"] == "0.940000"


def test_rate_rounding_up_to_one_is_rejected() -> None:
    with pytest.raises(ValueError):
        validate_cost_model_block({**VALID_MODEL, "tax_rate": "0.9999995"})


@pytest.mark.parametrize("role", ["", "   ", "x" * 101])
def test_invalid_role_names_rejected(role: str) -> None:
    with pytest.raises(ValueError):
        validate_cost_model_block({**VALID_MODEL, "labor_rates": {role: "1200"}})


def test_too_many_roles_rejected() -> None:
    rates = {f"岗位{i}": "1000" for i in range(51)}
    with pytest.raises(ValueError):
        validate_cost_model_block({**VALID_MODEL, "labor_rates": rates})
    assert validate_cost_model_block(
        {**VALID_MODEL, "labor_rates": {f"岗位{i}": "1000" for i in range(50)}}
    )["labor_rates"]


def test_duplicate_role_after_strip_rejected() -> None:
    with pytest.raises(ValueError):
        validate_cost_model_block(
            {**VALID_MODEL, "labor_rates": {"工程师": "1200", " 工程师": "1300"}}
        )


@pytest.mark.parametrize("bad", ["0", "-1", "abc", None, "1200.005", "1" * 19])
def test_invalid_labor_rates_rejected(bad: object) -> None:
    with pytest.raises(ValueError):
        validate_cost_model_block({**VALID_MODEL, "labor_rates": {"工程师": bad}})


def test_labor_rates_must_be_object() -> None:
    with pytest.raises(ValueError):
        validate_cost_model_block(
            {**VALID_MODEL, "labor_rates": [{"role": "工程师", "daily_rate": "1200"}]}
        )


def test_valid_margins_normalize_to_six_decimal_strings() -> None:
    assert validate_target_margins_block(VALID_MARGINS) == {
        "conservative": "0.120000",
        "balanced": "0.080000",
        "aggressive": "0.040000",
    }


@pytest.mark.parametrize("key", ["conservative", "balanced", "aggressive"])
@pytest.mark.parametrize("bad", ["-0.01", "1", "1.2", "abc", None])
def test_invalid_margins_rejected(key: str, bad: object) -> None:
    with pytest.raises(ValueError):
        validate_target_margins_block({**VALID_MARGINS, key: bad})


def test_missing_margin_key_rejected() -> None:
    with pytest.raises(ValueError):
        validate_target_margins_block({"conservative": "0.12", "balanced": "0.08"})


def test_omitted_blocks_produce_no_updates() -> None:
    only_model = build_setting_updates(
        PricingSettingsUpdate(cost_model=VALID_MODEL)
    )
    assert set(only_model) == {"pricing_cost_model"}
    only_margins = build_setting_updates(
        PricingSettingsUpdate(target_margins=VALID_MARGINS)
    )
    assert set(only_margins) == {"pricing_target_margins"}
    assert build_setting_updates(PricingSettingsUpdate()) == {}
    both = build_setting_updates(
        PricingSettingsUpdate(cost_model=VALID_MODEL, target_margins=VALID_MARGINS)
    )
    assert set(both) == {"pricing_cost_model", "pricing_target_margins"}