from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

MIN_BAND_SAMPLES = 5
MIN_WIN_RATE_SAMPLES = 8
BUDGET_LOW_FACTOR = Decimal("0.70")
BUDGET_HIGH_FACTOR = Decimal("1.40")
WIN_RATE_MIN = Decimal("0.05")
WIN_RATE_MAX = Decimal("0.95")

DISCLAIMER = "启发式估算，非模型预测，非中标承诺；仅供报价决策参考。"


def _append_once(items: list[str], value: str) -> None:
    if value not in items:
        items.append(value)


def percentile(values: Sequence[Decimal], quantile: Decimal) -> Decimal | None:
    """线性插值分位数（与常见 P25/P50/P75 口径一致），金额全程 Decimal。"""
    if not values:
        return None
    if quantile < 0 or quantile > 1:
        raise ValueError("quantile must be between 0 and 1")
    ordered = sorted(values)
    position = Decimal(len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - Decimal(lower)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


@dataclass(frozen=True)
class PriceBand:
    status: str
    sample_count: int
    p25: Decimal | None
    p50: Decimal | None
    p75: Decimal | None
    decided_count: int
    won_count: int
    lost_count: int
    price_loss_count: int
    base_win_rate: Decimal | None
    samples_by_source: dict[str, int] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class WinRateEstimate:
    rate: Decimal | None
    status: str
    basis: str
    heuristic: bool = True
    disclaimer: str = DISCLAIMER


def normalize_industry(values: Iterable[str] | None) -> list[str]:
    result: list[str] = []
    for value in values or []:
        text = str(value).strip().lower()
        if text and text not in result:
            result.append(text)
    return result


def in_scope(
    *,
    target_industries: Sequence[str],
    sample_industries: Sequence[str],
    target_budget: Decimal | None,
    sample_budget: Decimal | None,
) -> bool:
    """同类项目口径：行业有交集 + 预算在目标预算的 70%～140% 区间。"""
    target = normalize_industry(target_industries)
    sample = normalize_industry(sample_industries)
    if not target or not sample:
        return False
    industry_matched = any(
        left == right or left in right or right in left
        for left in target
        for right in sample
    )
    if not industry_matched:
        return False
    if target_budget is None or sample_budget is None or target_budget <= 0 or sample_budget <= 0:
        return False
    return target_budget * BUDGET_LOW_FACTOR <= sample_budget <= target_budget * BUDGET_HIGH_FACTOR


def build_price_band(
    *,
    award_ratios: Sequence[Decimal],
    decided_outcomes: Sequence[str],
    price_loss_count: int,
    samples_by_source: dict[str, int] | None = None,
    min_samples: int = MIN_BAND_SAMPLES,
    min_win_rate_samples: int = MIN_WIN_RATE_SAMPLES,
) -> PriceBand:
    ratios = [value for value in award_ratios if value > 0]
    won = sum(1 for outcome in decided_outcomes if outcome == "won")
    lost = sum(1 for outcome in decided_outcomes if outcome == "lost")
    decided = won + lost
    missing: list[str] = []
    notes: list[str] = []

    if len(ratios) < min_samples:
        missing.append(
            f"同类项目中标价/预算比样本不足（需至少 {min_samples} 条，当前 {len(ratios)} 条）"
        )
        status = "insufficient_data"
        p25 = p50 = p75 = None
    else:
        status = "ok"
        p25 = percentile(ratios, Decimal("0.25"))
        p50 = percentile(ratios, Decimal("0.50"))
        p75 = percentile(ratios, Decimal("0.75"))

    base_win_rate: Decimal | None = None
    if decided >= min_win_rate_samples and decided > 0:
        base_win_rate = Decimal(won) / Decimal(decided)
    else:
        notes.append(
            f"同类项目已决标样本不足（需至少 {min_win_rate_samples} 条，当前 {decided} 条），不输出赢率估计"
        )

    if price_loss_count:
        notes.append(f"同类项目中有 {price_loss_count} 条因「报价」失标记录，已用于提示价格竞争强度")

    return PriceBand(
        status=status,
        sample_count=len(ratios),
        p25=p25,
        p50=p50,
        p75=p75,
        decided_count=decided,
        won_count=won,
        lost_count=lost,
        price_loss_count=price_loss_count,
        base_win_rate=base_win_rate,
        samples_by_source=dict(samples_by_source or {}),
        missing=missing,
        notes=notes,
    )


def estimate_win_rate(
    *,
    quote_ratio: Decimal | None,
    band: PriceBand,
) -> WinRateEstimate:
    """按报价相对历史价格带的位置做粗略赢率平移，不训练模型、不承诺结果。"""
    if quote_ratio is None:
        return WinRateEstimate(None, "insufficient_data", "缺少报价/预算比，无法估计赢率")
    if band.status != "ok" or band.p50 is None:
        return WinRateEstimate(None, "insufficient_data", "历史价格带样本不足，无法估计赢率")
    if band.decided_count < MIN_WIN_RATE_SAMPLES or band.base_win_rate is None:
        return WinRateEstimate(
            None,
            "insufficient_data",
            "同类项目已决标样本不足，无法估计赢率",
        )

    if band.p25 is not None and quote_ratio <= band.p25:
        position = "报价不高于历史 P25，价格位置偏进取"
    elif band.p50 is not None and quote_ratio <= band.p50:
        position = "报价位于历史 P25～P50，价格位置略有竞争力"
    elif band.p75 is not None and quote_ratio <= band.p75:
        position = "报价位于历史 P50～P75，价格位置偏保守"
    else:
        position = "报价高于历史 P75，价格位置明显偏高"

    # 连续平移：以 P50 为中心，用 P25～P75 带宽归一化，避免同一分位桶内
    # 报价变化时赢率完全不动；该系数是启发式假设，不是拟合参数。
    if band.p25 is not None and band.p50 is not None and band.p75 is not None:
        spread = max(band.p75 - band.p25, band.p50 * Decimal("0.05"), Decimal("0.0001"))
        normalized = (band.p50 - quote_ratio) / spread
        adjustment = max(Decimal("-0.20"), min(Decimal("0.20"), normalized * Decimal("0.24")))
    else:
        adjustment = Decimal("0")

    rate = band.base_win_rate + adjustment
    rate = max(WIN_RATE_MIN, min(WIN_RATE_MAX, rate))
    basis = (
        f"以同类项目历史胜率 {band.base_win_rate:.2%} 为基准，按{position}平移 "
        f"{adjustment:+.2%}；样本共 {band.decided_count} 条已决标记录。"
    )
    return WinRateEstimate(rate=rate, status="ok", basis=basis)
