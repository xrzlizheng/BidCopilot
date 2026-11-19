from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

MONEY_QUANT = Decimal("0.01")
RATE_QUANT = Decimal("0.000001")


def decimal_or_none(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _append_once(items: list[str], value: str) -> None:
    if value not in items:
        items.append(value)


@dataclass(frozen=True)
class LaborLine:
    role: str
    days: Decimal


@dataclass(frozen=True)
class CostModel:
    direct_rate: Decimal | None
    indirect_rate: Decimal | None
    tax_rate: Decimal | None
    labor_rates: dict[str, Decimal]
    source: str
    missing: tuple[str, ...] = ()


@dataclass(frozen=True)
class CostBaseline:
    status: str
    quote: Decimal | None
    labor_cost: Decimal | None
    other_direct_cost: Decimal | None
    direct_cost: Decimal | None
    indirect_cost: Decimal | None
    tax_cost: Decimal | None
    total_cost: Decimal | None
    net_profit: Decimal | None
    net_margin: Decimal | None
    direct_cost_ratio: Decimal | None
    direct_rate_variance: Decimal | None
    missing: list[str] = field(default_factory=list)
    source_labels: dict[str, str] = field(default_factory=dict)


def parse_cost_model(raw: dict[str, Any] | None, *, source: str) -> CostModel:
    """解析租户成本参数；缺项保留为 None，绝不填默认费率。"""
    data = raw or {}
    missing: list[str] = []

    def _rate(key: str, label: str) -> Decimal | None:
        value = decimal_or_none(data.get(key))
        if value is None:
            _append_once(missing, f"租户成本参数缺少 {label}（{key}）")
            return None
        if value < 0 or value >= 1:
            _append_once(missing, f"租户成本参数 {key} 必须在 [0, 1) 范围内")
            return None
        return value

    direct_rate = _rate("direct_rate", "直接成本率")
    indirect_rate = _rate("indirect_rate", "间接成本率")
    tax_rate = _rate("tax_rate", "税率")

    labor_rates: dict[str, Decimal] = {}
    raw_rates = data.get("labor_rates")
    invalid_roles: list[str] = []
    if isinstance(raw_rates, dict):
        for role, rate_value in raw_rates.items():
            role_name = str(role).strip()
            rate = decimal_or_none(rate_value)
            if role_name and rate is not None and rate > 0:
                labor_rates[role_name] = rate
            elif role_name:
                invalid_roles.append(role_name)
    elif isinstance(raw_rates, list):
        for item in raw_rates:
            if not isinstance(item, dict):
                continue
            role_name = str(item.get("role") or "").strip()
            rate = decimal_or_none(item.get("daily_rate"))
            if role_name and rate is not None and rate > 0:
                labor_rates[role_name] = rate
            elif role_name:
                invalid_roles.append(role_name)
    if invalid_roles:
        _append_once(
            missing,
            "租户人力单价无效：" + "、".join(invalid_roles[:5]),
        )

    return CostModel(
        direct_rate=direct_rate,
        indirect_rate=indirect_rate,
        tax_rate=tax_rate,
        labor_rates=labor_rates,
        source=source,
        missing=tuple(missing),
    )


def build_cost_baseline(
    model: CostModel,
    *,
    labor_items: Sequence[LaborLine],
    other_direct_cost: Decimal | None,
    quote: Decimal | None,
) -> CostBaseline:
    """绝对成本口径：人力投入 + 其他直接成本 + 报价比例间接费/税。

    直接成本率（direct_rate）不用于倒推本单成本，而是作为「直接成本占报价」
    的租户校准基准；实际净利率始终按本单绝对成本计算，避免用比率掩盖成本。
    """
    missing: list[str] = list(model.missing)
    source_labels = {
        "rates": model.source,
        "labor_rates": model.source,
        "other_direct_cost": "input" if other_direct_cost is not None else "missing",
        "quote": "input" if quote is not None and quote > 0 else "missing",
    }

    labor_cost: Decimal | None = None
    if labor_items:
        total = Decimal("0")
        roles_ok = True
        for item in labor_items:
            rate = model.labor_rates.get(item.role)
            if rate is None:
                roles_ok = False
                _append_once(missing, f"租户人力单价缺少岗位：{item.role}")
                continue
            if item.days <= 0:
                roles_ok = False
                _append_once(missing, f"人力投入天数必须大于 0：{item.role}")
                continue
            total += item.days * rate
        if roles_ok:
            labor_cost = total
    else:
        _append_once(missing, "缺少人力投入明细（角色与天数）")

    if other_direct_cost is None:
        _append_once(missing, "缺少其他直接成本（若无请显式填写 0）")
    elif other_direct_cost < 0:
        _append_once(missing, "其他直接成本不能为负数")
        other_direct_cost = None

    direct_cost = (
        labor_cost + other_direct_cost
        if labor_cost is not None and other_direct_cost is not None
        else None
    )

    if quote is None:
        _append_once(missing, "缺少候选报价")
    elif quote <= 0:
        _append_once(missing, "候选报价必须大于 0")
        quote = None

    indirect_cost: Decimal | None = None
    tax_cost: Decimal | None = None
    total_cost: Decimal | None = None
    net_profit: Decimal | None = None
    net_margin: Decimal | None = None
    direct_cost_ratio: Decimal | None = None
    direct_rate_variance: Decimal | None = None

    if quote is not None and direct_cost is not None:
        direct_cost_ratio = direct_cost / quote
        if model.direct_rate is not None:
            direct_rate_variance = direct_cost_ratio - model.direct_rate
        if model.indirect_rate is not None and model.tax_rate is not None:
            if model.indirect_rate + model.tax_rate >= 1:
                _append_once(missing, "间接成本率与税率之和必须小于 1")
            else:
                indirect_cost = quote * model.indirect_rate
                tax_cost = quote * model.tax_rate
                total_cost = direct_cost + indirect_cost + tax_cost
                net_profit = quote - total_cost
                net_margin = net_profit / quote

    status = (
        "ok"
        if net_margin is not None and total_cost is not None and not missing
        else "insufficient_data"
    )
    return CostBaseline(
        status=status,
        quote=quote,
        labor_cost=labor_cost,
        other_direct_cost=other_direct_cost,
        direct_cost=direct_cost,
        indirect_cost=indirect_cost,
        tax_cost=tax_cost,
        total_cost=total_cost,
        net_profit=net_profit,
        net_margin=net_margin,
        direct_cost_ratio=direct_cost_ratio,
        direct_rate_variance=direct_rate_variance,
        missing=missing,
        source_labels=source_labels,
    )


def solve_quote_for_margin(
    *,
    direct_cost: Decimal,
    indirect_rate: Decimal,
    tax_rate: Decimal,
    target_margin: Decimal,
) -> tuple[Decimal | None, str | None]:
    """按绝对成本 + 报价比例间接费/税，反解目标净利率对应的报价。"""
    denominator = Decimal("1") - indirect_rate - tax_rate - target_margin
    if denominator <= 0:
        return None, "目标净利率过高，间接成本率/税率/目标净利率之和不小于 1，无法反解报价"
    quote = direct_cost / denominator
    if quote <= 0:
        return None, "反解报价不为正"
    return quote.quantize(MONEY_QUANT), None


def net_margin_at_quote(
    *,
    quote: Decimal,
    direct_cost: Decimal,
    indirect_rate: Decimal,
    tax_rate: Decimal,
) -> Decimal | None:
    if quote <= 0:
        return None
    total = direct_cost + quote * (indirect_rate + tax_rate)
    return (quote - total) / quote
