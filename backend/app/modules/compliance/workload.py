import re
from typing import Any

from app.modules.compliance.registry import Rule

MANDAY_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*(?:人天|人·天|工日)")
HEADCOUNT_PATTERN = re.compile(r"(?:投入|配备|安排|派驻|配置)?\s*(\d+)\s*(?:名|人)(?:的|、)?(?:项目|技术|实施|交付|服务)?人员")
DURATION_PATTERN = re.compile(r"(?:工期|实施周期|服务周期|驻场周期|开发周期)\s*(?:为|约|:|：)?\s*(\d+(?:\.\d+)?)\s*(天|日|个月|月|周)")


def _duration_days(value: float, unit: str) -> float:
    if unit in {"个月", "月"}:
        return value * 30
    if unit == "周":
        return value * 7
    return value


def _quote_amount(context: Any) -> float | None:
    """取投标报价：优先封面/投标函章节，取不到则回退正文。"""
    sections = getattr(context, "sections", None) or []
    if sections:
        from app.modules.compliance.engine import _extract_bid_price

        value = _extract_bid_price(context)
        if value is not None:
            return value
    text = context.content_text or ""
    match = re.search(
        r"(?:投标总价|投标报价|总报价|报价总额|投标价格)[^0-9]{0,16}"
        r"(\d[\d,，]*(?:\.\d+)?)\s*(万元|元)",
        text,
    )
    if match is None:
        return None
    raw = match.group(1).replace(",", "").replace("，", "")
    try:
        amount = float(raw)
    except ValueError:
        return None
    return amount * 10000 if match.group(2) == "万元" else amount


def estimate_workload_cost(text: str) -> tuple[float, str] | None:
    """从正文中估工作量（人天）。只认明确写法，不做推断。"""
    direct = MANDAY_PATTERN.search(text)
    if direct:
        return float(direct.group(1)), "直接写明人天"
    headcount = HEADCOUNT_PATTERN.search(text)
    duration = DURATION_PATTERN.search(text)
    if headcount and duration:
        days = _duration_days(float(duration.group(1)), duration.group(2))
        return float(headcount.group(1)) * days, "人数×工期"
    return None


def check_workload_cost(rule: Rule, context: Any, finding: Any) -> list[dict[str, Any]]:
    """按租户校准的人天单价估算人力成本；估算成本高于报价时提示，不做法律认定。"""
    cost_model = getattr(context, "cost_model", None) or {}
    rate = cost_model.get("person_day_rate")
    if rate is None or rate == "":
        return []
    try:
        person_day_rate = float(rate)
    except (TypeError, ValueError):
        return []
    if person_day_rate <= 0:
        return []

    text = context.content_text or ""
    if not text.strip():
        return []
    workload = estimate_workload_cost(text)
    quote = _quote_amount(context)
    if workload is None or quote is None:
        return []
    person_days, basis = workload
    if person_days <= 0:
        return []
    estimated = person_days * person_day_rate
    if estimated <= quote:
        return []
    return [
        finding(
            rule,
            message="按人天单价估算的人力成本高于投标报价",
            location={"scope": "content"},
            expected=f"人力成本 ≤ 报价 {quote:.2f}",
            actual=f"{person_days:g} 人天 × {person_day_rate:g} = {estimated:.2f}",
            suggestion="核对工作量口径或报价构成；如为口径差异请人工说明，不要直接调价。",
        )
    ]
