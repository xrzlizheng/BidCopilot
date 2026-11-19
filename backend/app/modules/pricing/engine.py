from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.modules.pricing.cost_model import (
    MONEY_QUANT,
    CostBaseline,
    net_margin_at_quote,
    solve_quote_for_margin,
)
from app.modules.pricing.price_band import (
    BUDGET_HIGH_FACTOR,
    BUDGET_LOW_FACTOR,
    DISCLAIMER,
    MIN_BAND_SAMPLES,
    MIN_WIN_RATE_SAMPLES,
    PriceBand,
    estimate_win_rate,
)

ENGINE_VERSION = "pricing-v1"
DEFAULT_TARGET_MARGINS: dict[str, Decimal] = {
    "conservative": Decimal("0.12"),
    "balanced": Decimal("0.08"),
    "aggressive": Decimal("0.04"),
}
TIER_LABELS = {
    "conservative": "保守",
    "balanced": "均衡",
    "aggressive": "进取",
}
SENSITIVITY_CHANGES = (
    Decimal("-0.08"),
    Decimal("-0.05"),
    Decimal("-0.03"),
    Decimal("0"),
    Decimal("0.03"),
    Decimal("0.05"),
    Decimal("0.08"),
)


def money_text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value.quantize(MONEY_QUANT), "f")


def rate_text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value.quantize(Decimal("0.000001")), "f")


def _append_unique(items: list[str], value: str) -> None:
    if value not in items:
        items.append(value)


def _quote_ratio(quote: Decimal | None, budget: Decimal | None) -> Decimal | None:
    if quote is None or budget is None or quote <= 0 or budget <= 0:
        return None
    return quote / budget


def _discount_rate(quote: Decimal | None, budget: Decimal | None) -> Decimal | None:
    ratio = _quote_ratio(quote, budget)
    return None if ratio is None else Decimal("1") - ratio


def _band_position(quote_ratio: Decimal | None, band: PriceBand) -> str:
    if quote_ratio is None or band.status != "ok" or band.p50 is None:
        return "历史价格带不足，无法判断价格位置"
    if band.p25 is not None and quote_ratio <= band.p25:
        return "不高于历史 P25（进取）"
    if quote_ratio <= band.p50:
        return "位于 P25～P50（略有竞争力）"
    if band.p75 is not None and quote_ratio <= band.p75:
        return "位于 P50～P75（偏保守）"
    return "高于历史 P75（明显偏保守）"


def cost_payload(cost: CostBaseline) -> dict[str, Any]:
    return {
        "status": cost.status,
        "quote_amount": money_text(cost.quote),
        "labor_cost": money_text(cost.labor_cost),
        "other_direct_cost": money_text(cost.other_direct_cost),
        "direct_cost": money_text(cost.direct_cost),
        "indirect_cost": money_text(cost.indirect_cost),
        "tax_cost": money_text(cost.tax_cost),
        "total_cost": money_text(cost.total_cost),
        "net_profit": money_text(cost.net_profit),
        "net_margin": rate_text(cost.net_margin),
        "direct_cost_ratio": rate_text(cost.direct_cost_ratio),
        "direct_rate_variance": rate_text(cost.direct_rate_variance),
        "missing": list(cost.missing),
        "source_labels": dict(cost.source_labels),
        "note": "直接成本率仅作为租户校准基准；净利率按本单绝对成本计算，不用比率替代成本。",
    }


def price_band_payload(band: PriceBand, budget: Decimal | None) -> dict[str, Any]:
    def _amount(ratio: Decimal | None) -> str | None:
        if ratio is None or budget is None:
            return None
        return money_text(ratio * budget)

    return {
        "status": band.status,
        "sample_count": band.sample_count,
        "p25": rate_text(band.p25),
        "p50": rate_text(band.p50),
        "p75": rate_text(band.p75),
        "p25_amount": _amount(band.p25),
        "p50_amount": _amount(band.p50),
        "p75_amount": _amount(band.p75),
        "decided_count": band.decided_count,
        "won_count": band.won_count,
        "lost_count": band.lost_count,
        "price_loss_count": band.price_loss_count,
        "base_win_rate": rate_text(band.base_win_rate),
        "samples_by_source": dict(band.samples_by_source),
        "missing": list(band.missing),
        "notes": list(band.notes),
        "disclaimer": DISCLAIMER,
    }


def _win_rate_payload(quote_ratio: Decimal | None, band: PriceBand) -> dict[str, Any]:
    estimate = estimate_win_rate(quote_ratio=quote_ratio, band=band)
    return {
        "status": estimate.status,
        "rate": rate_text(estimate.rate),
        "basis": estimate.basis,
        "heuristic": estimate.heuristic,
        "disclaimer": estimate.disclaimer,
    }


def build_sensitivity(
    *,
    base_quote: Decimal | None,
    base_source: str,
    budget: Decimal | None,
    direct_cost: Decimal | None,
    indirect_rate: Decimal | None,
    tax_rate: Decimal | None,
    band: PriceBand,
) -> dict[str, Any]:
    if base_quote is None or direct_cost is None or indirect_rate is None or tax_rate is None:
        return {
            "status": "insufficient_data",
            "base_quote": money_text(base_quote),
            "base_source": base_source,
            "rows": [],
            "missing": ["缺少基准报价或完整成本参数，无法做敏感性分析"],
            "disclaimer": DISCLAIMER,
        }
    rows: list[dict[str, Any]] = []
    for change in SENSITIVITY_CHANGES:
        quote = (base_quote * (Decimal("1") + change)).quantize(MONEY_QUANT)
        margin = net_margin_at_quote(
            quote=quote,
            direct_cost=direct_cost,
            indirect_rate=indirect_rate,
            tax_rate=tax_rate,
        )
        win = _win_rate_payload(_quote_ratio(quote, budget), band)
        rows.append(
            {
                "change_rate": rate_text(change),
                "quote_amount": money_text(quote),
                "net_margin": rate_text(margin),
                "discount_rate": rate_text(_discount_rate(quote, budget)),
                "win_rate": win["rate"],
                "win_rate_status": win["status"],
                "below_cost": margin is not None and margin < 0,
                "band_position": _band_position(_quote_ratio(quote, budget), band),
            }
        )
    return {
        "status": "ok",
        "base_quote": money_text(base_quote),
        "base_source": base_source,
        "rows": rows,
        "missing": [],
        "disclaimer": DISCLAIMER,
    }


def cost_floor_price(
    *,
    direct_cost: Decimal | None,
    indirect_rate: Decimal | None,
    tax_rate: Decimal | None,
) -> Decimal | None:
    """净利率为 0 的报价下限：price * (1 - indirect - tax) = direct_cost。"""
    if direct_cost is None or indirect_rate is None or tax_rate is None:
        return None
    remaining = Decimal("1") - indirect_rate - tax_rate
    if remaining <= 0:
        return None
    return (direct_cost / remaining).quantize(MONEY_QUANT)


MARKET_ANCHOR_LABELS = {
    "conservative": "P75（偏保守）",
    "balanced": "P50（市场中位）",
    "aggressive": "P25（偏进取）",
}


def _market_anchor(
    key: str, band: PriceBand, budget: Decimal | None
) -> tuple[Decimal | None, Decimal | None]:
    """市场锚定：返回（锚定比例, 锚定金额）。价格带不可用或缺预算时为 None。"""
    if band.status != "ok" or budget is None or budget <= 0:
        return None, None
    ratio = {"conservative": band.p75, "balanced": band.p50, "aggressive": band.p25}[key]
    if ratio is None or ratio <= 0:
        return None, None
    return ratio, (ratio * budget).quantize(MONEY_QUANT)


def build_tiers(
    *,
    direct_cost: Decimal | None,
    indirect_rate: Decimal | None,
    tax_rate: Decimal | None,
    budget: Decimal | None,
    band: PriceBand,
    target_margins: dict[str, Decimal],
) -> list[dict[str, Any]]:
    """三档建议价。

    定价顺序：优先用历史价格带锚定（保守=P75 / 均衡=P50 / 进取=P25），
    再用成本底线做下限、招标预算做上限钳制；历史价格带不足时退回成本加成反解。
    成本加成价始终作为对照字段返回，便于复核两者差异。
    """
    if direct_cost is None or indirect_rate is None or tax_rate is None:
        return []
    risks_by_tier = {
        "conservative": ["价格偏高可能降低竞争力", "若高于招标预算，需核对预算口径与废标风险"],
        "balanced": ["兼顾毛利与竞争力，但仍取决于竞争对手报价", "成本超支会直接侵蚀净利"],
        "aggressive": ["毛利空间最薄，成本超支或评审压价时风险最高", "低价可能触发异常低价审查"],
    }
    floor = cost_floor_price(
        direct_cost=direct_cost, indirect_rate=indirect_rate, tax_rate=tax_rate
    )
    tiers: list[dict[str, Any]] = []
    for key in ("conservative", "balanced", "aggressive"):
        target_margin = target_margins[key]
        cost_plus_price, solve_error = solve_quote_for_margin(
            direct_cost=direct_cost,
            indirect_rate=indirect_rate,
            tax_rate=tax_rate,
            target_margin=target_margin,
        )
        anchor_ratio, anchor_price = _market_anchor(key, band, budget)
        clamp_notes: list[str] = []
        if anchor_price is not None:
            price: Decimal | None = anchor_price
            price_source = "market_band"
        else:
            price = cost_plus_price
            price_source = "cost_plus"
        if price is None:
            tiers.append(
                {
                    "tier": key,
                    "label": TIER_LABELS[key],
                    "status": "insufficient_data",
                    "price": None,
                    "price_source": "none",
                    "target_margin": rate_text(target_margin),
                    "reason": solve_error or "无法反解报价，且历史价格带不足",
                    "risks": risks_by_tier[key],
                    "clamp_notes": [],
                }
            )
            continue
        if floor is not None and price < floor:
            clamp_notes.append(
                f"市场锚定价低于成本底线，已上调至 {money_text(floor)} 元（净利率 0%）"
            )
            price = floor
            price_source = "market_band_cost_floor"
        if budget is not None and price > budget:
            clamp_notes.append(
                f"锚定价高于招标预算，已下调至预算 {money_text(budget)} 元"
            )
            price = budget
            price_source = "market_band_budget_cap"
        # 预算上限把价格压到成本底线以下：该档不可执行，整体状态不得仍为 ok。
        below_cost_floor = floor is not None and price < floor
        actual_margin = net_margin_at_quote(
            quote=price,
            direct_cost=direct_cost,
            indirect_rate=indirect_rate,
            tax_rate=tax_rate,
        )
        quote_ratio = _quote_ratio(price, budget)
        win = _win_rate_payload(quote_ratio, band)
        risks = list(risks_by_tier[key])
        if actual_margin is not None and actual_margin < 0:
            risks.append("该档净利率为负，不建议采用")
        elif actual_margin is not None and actual_margin < target_margin:
            risks.append(
                f"实际净利率 {actual_margin:.2%} 低于该档目标 {target_margin:.2%}，需确认是否可接受"
            )
        if floor is not None and price == floor:
            risks.append("已触及成本底线，不建议继续让价")
        if below_cost_floor:
            risks.append("招标预算低于成本底线，该档报价不可执行")
        if win["rate"] is None:
            risks.append("历史样本不足，无法给出赢率估计")
        if price_source == "market_band":
            reason = (
                f"按同类项目历史价格带 {MARKET_ANCHOR_LABELS[key]} 锚定；"
                f"实际净利率 {actual_margin:.2%}（该档目标 {target_margin:.2%}）；"
                f"价格位置：{_band_position(quote_ratio, band)}。"
            )
        else:
            reason = (
                f"历史价格带不足，退回成本加成：按目标净利率 {target_margin:.2%} 反解；"
                f"实际净利率 {actual_margin:.2%}；价格位置：{_band_position(quote_ratio, band)}。"
            )
        tiers.append(
            {
                "tier": key,
                "label": TIER_LABELS[key],
                "status": "insufficient_data" if below_cost_floor else "ok",
                "price": money_text(price),
                "price_source": price_source,
                "market_anchor_ratio": rate_text(anchor_ratio),
                "market_anchor_price": money_text(anchor_price),
                "cost_plus_price": money_text(cost_plus_price),
                "cost_floor_price": money_text(floor),
                "target_margin": rate_text(target_margin),
                "net_margin": rate_text(actual_margin),
                "discount_rate": rate_text(_discount_rate(price, budget)),
                "quote_ratio": rate_text(quote_ratio),
                "win_rate": win["rate"],
                "win_rate_status": win["status"],
                "win_rate_basis": win["basis"],
                "band_position": _band_position(quote_ratio, band),
                "clamp_notes": clamp_notes,
                "reason": reason,
                "risks": risks,
            }
        )
    return tiers


def build_strategy_payload(
    *,
    budget: Decimal | None,
    budget_source: str,
    industry: list[str],
    industry_source: str,
    quote: Decimal | None,
    cost: CostBaseline,
    band: PriceBand,
    indirect_rate: Decimal | None,
    tax_rate: Decimal | None,
    target_margins: dict[str, Decimal],
    target_margins_source: str,
) -> dict[str, Any]:
    missing: list[str] = []
    for value in cost.missing:
        _append_unique(missing, value)
    for value in band.missing:
        _append_unique(missing, value)
    if budget is None:
        _append_unique(missing, "缺少项目预算金额（招标文件未解析出预算，且请求未提供）")
    if not industry:
        _append_unique(missing, "缺少项目行业，无法筛选同类历史项目")
    if band.base_win_rate is None:
        _append_unique(
            missing,
            "同类项目已决标样本不足，无法给出赢率估计（价格带可展示，但策略结论不完整）",
        )

    tiers = build_tiers(
        direct_cost=cost.direct_cost,
        indirect_rate=indirect_rate,
        tax_rate=tax_rate,
        budget=budget,
        band=band,
        target_margins=target_margins,
    )
    balanced_price: Decimal | None = None
    for item in tiers:
        if item.get("tier") == "balanced" and item.get("price") is not None:
            balanced_price = Decimal(str(item["price"]))
            break
    base_quote = quote if quote is not None and quote > 0 else balanced_price
    base_source = "input" if quote is not None and quote > 0 else "balanced_tier"

    sensitivity = build_sensitivity(
        base_quote=base_quote,
        base_source=base_source,
        budget=budget,
        direct_cost=cost.direct_cost,
        indirect_rate=indirect_rate,
        tax_rate=tax_rate,
        band=band,
    )

    candidate: dict[str, Any] | None = None
    if quote is not None and quote > 0:
        quote_ratio = _quote_ratio(quote, budget)
        win = _win_rate_payload(quote_ratio, band)
        candidate = {
            "quote_amount": money_text(quote),
            "quote_ratio": rate_text(quote_ratio),
            "discount_rate": rate_text(_discount_rate(quote, budget)),
            "net_margin": rate_text(cost.net_margin),
            "total_cost": money_text(cost.total_cost),
            "band_position": _band_position(quote_ratio, band),
            "win_rate": win,
        }

    all_tiers_ok = bool(tiers) and all(item.get("status") == "ok" for item in tiers)
    status = (
        "ok"
        if cost.status == "ok"
        and band.status == "ok"
        and budget is not None
        and bool(industry)
        and all_tiers_ok
        and not missing
        else "insufficient_data"
    )

    return {
        "status": status,
        "engine_version": ENGINE_VERSION,
        "disclaimer": DISCLAIMER,
        "missing": missing,
        "budget": {"amount": money_text(budget), "source": budget_source},
        "industry": {"values": list(industry), "source": industry_source},
        "cost": cost_payload(cost),
        "price_band": price_band_payload(band, budget),
        "candidate": candidate,
        "tiers": tiers,
        "sensitivity": sensitivity,
        "assumptions": {
            "target_margins": {key: rate_text(value) for key, value in target_margins.items()},
            "target_margins_source": target_margins_source,
            "budget_low_factor": rate_text(BUDGET_LOW_FACTOR),
            "budget_high_factor": rate_text(BUDGET_HIGH_FACTOR),
            "min_band_samples": MIN_BAND_SAMPLES,
            "min_win_rate_samples": MIN_WIN_RATE_SAMPLES,
            "note": "目标净利率档位是工具默认假设（可由租户配置覆盖），不是历史样本拟合结果。",
        },
    }


def build_narrative_prompt(payload: dict[str, Any]) -> str:
    return (
        "以下是一次报价策略计算的真实结果（JSON）。请用中文写一段不超过 350 字的报价策略说明，"
        "必须：1) 引用给出的价格带/成本/建议价数字；2) 明确说明这是启发式估计，非模型预测、非中标承诺；"
        "3) 不得编造未给出的历史样本或竞争对手信息；4) 数据不足时直说缺什么。\n\n"
        f"{payload}"
    )


def template_narrative(payload: dict[str, Any]) -> str:
    status = payload.get("status")
    band = payload.get("price_band") or {}
    cost = payload.get("cost") or {}
    if status != "ok":
        missing = "；".join(payload.get("missing") or ["未知缺项"])
        return (
            "当前数据不足，不能给出可复核的报价结论。缺少：" + missing + "。"
            "已展示的成本拆解与历史样本均来自现有租户配置/业务记录，未使用默认值补齐。"
            "本说明为启发式提示，不是模型预测，也不是中标承诺。"
        )
    balanced = next(
        (item for item in payload.get("tiers") or [] if item.get("tier") == "balanced"),
        None,
    )
    win = (balanced or {}).get("win_rate")
    win_text = f"{Decimal(win):.2%}" if win is not None else "样本不足，无法估计"
    return (
        f"同类历史中标价/预算比价格带为 P25 {band.get('p25')} / P50 {band.get('p50')} / "
        f"P75 {band.get('p75')}；本单直接成本 {cost.get('direct_cost')} 元，"
        f"按目标净利率反解后均衡建议价 {((balanced or {}).get('price'))} 元，"
        f"对应净利率 {((balanced or {}).get('net_margin'))}、粗略赢率估计 {win_text}。"
        "该赢率仅按历史样本与报价位置做启发式平移，不是模型预测，也不构成中标承诺；"
        "最终报价仍需结合竞争情报、交付风险与人工审批。"
    )
