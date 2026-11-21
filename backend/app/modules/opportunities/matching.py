from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import ColumnElement, case, false, literal, or_

from app.core.like import like_contains
from app.modules.opportunities.models import Opportunity, OpportunitySubscription

KEYWORD_WEIGHT = 2
REGION_WEIGHT = 2
INDUSTRY_WEIGHT = 2
BUDGET_WEIGHT = 1


@dataclass(frozen=True)
class MatchPreference:
    keywords: tuple[str, ...] = ()
    regions: tuple[str, ...] = ()
    industries: tuple[str, ...] = ()
    budget_min: Decimal | None = None
    budget_max: Decimal | None = None

    @property
    def has_conditions(self) -> bool:
        return bool(
            self.keywords
            or self.regions
            or self.industries
            or self.budget_min is not None
            or self.budget_max is not None
        )

    def budget_contains(self, amount: Decimal) -> bool:
        if self.budget_min is not None and amount < self.budget_min:
            return False
        if self.budget_max is not None and amount > self.budget_max:
            return False
        return self.budget_min is not None or self.budget_max is not None


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def preference_from_query(query: dict[str, Any]) -> MatchPreference:
    keywords: list[str] = []
    regions: list[str] = []
    industries: list[str] = []
    for item in query.get("keywords") or []:
        text = str(item).strip()
        if text:
            keywords.append(text)
    for entry in query.get("region_list") or []:
        if isinstance(entry, dict):
            regions += [str(value).strip() for value in entry.values() if str(value).strip()]
    for item in query.get("industry_list") or []:
        text = str(item).strip()
        if text:
            industries.append(text)
    raw_min = query.get("budget_min")
    raw_max = query.get("budget_max")
    return MatchPreference(
        keywords=_unique(keywords),
        regions=_unique(regions),
        industries=_unique(industries),
        budget_min=Decimal(str(raw_min)) if raw_min not in (None, "") else None,
        budget_max=Decimal(str(raw_max)) if raw_max not in (None, "") else None,
    )


def preferences_from_subscriptions(
    subscriptions: Iterable[OpportunitySubscription],
) -> list[MatchPreference]:
    preferences = []
    for subscription in subscriptions:
        preference = preference_from_query(subscription.query_json or {})
        if preference.has_conditions:
            preferences.append(preference)
    return preferences


def _keyword_expression(preference: MatchPreference) -> ColumnElement[bool]:
    if not preference.keywords:
        return false()
    return or_(
        *[
            Opportunity.title.ilike(like_contains(keyword), escape="\\")
            for keyword in preference.keywords
        ]
    )


def _region_expression(preference: MatchPreference) -> ColumnElement[bool]:
    if not preference.regions:
        return false()
    return or_(
        *[
            Opportunity.region.ilike(like_contains(region), escape="\\")
            for region in preference.regions
        ]
    )


def _industry_expression(preference: MatchPreference) -> ColumnElement[bool]:
    if not preference.industries:
        return false()
    return or_(*[Opportunity.industry.contains([item]) for item in preference.industries])


def _budget_expression(preference: MatchPreference) -> ColumnElement[bool]:
    # 单个订阅的预算区间必须整体满足；跨订阅取“任一订阅命中”，不做区间并集。
    lower = (
        Opportunity.budget_amount >= preference.budget_min
        if preference.budget_min is not None
        else None
    )
    upper = (
        Opportunity.budget_amount <= preference.budget_max
        if preference.budget_max is not None
        else None
    )
    if lower is not None and upper is not None:
        return lower & upper
    return lower if lower is not None else upper if upper is not None else false()


def match_score_expression(preferences: list[MatchPreference]) -> ColumnElement[int]:
    """启发式加权：每个订阅的关键词/地区/行业各 2 分、预算区间 1 分后求和。不是模型评分。"""
    score: ColumnElement[int] | None = None
    for preference in preferences:
        part = (
            case((_keyword_expression(preference), KEYWORD_WEIGHT), else_=0)
            + case((_region_expression(preference), REGION_WEIGHT), else_=0)
            + case((_industry_expression(preference), INDUSTRY_WEIGHT), else_=0)
            + case((_budget_expression(preference), BUDGET_WEIGHT), else_=0)
        )
        score = part if score is None else score + part
    return score if score is not None else literal(0)


def count_match_hits(preferences: list[MatchPreference], opportunity: Opportunity) -> int:
    """命中条件种类数（关键词/地区/行业/预算，0-4），与排序同源，供界面如实标注。"""
    title = (opportunity.title or "").casefold()
    region = (opportunity.region or "").casefold()
    industries = set(opportunity.industry or [])
    budget = opportunity.budget_amount
    groups: set[str] = set()
    for preference in preferences:
        if preference.keywords and any(
            keyword.casefold() in title for keyword in preference.keywords
        ):
            groups.add("keyword")
        if preference.regions and any(item.casefold() in region for item in preference.regions):
            groups.add("region")
        if preference.industries and set(preference.industries) & industries:
            groups.add("industry")
        if budget is not None and preference.budget_contains(budget):
            groups.add("budget")
    return len(groups)


def explain_opportunity(
    preferences: list[MatchPreference],
    opportunity: Opportunity,
    *,
    buyer_watched: bool = False,
    buyer_intel: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """给出可复核的推荐理由/风险点；只是启发式解释，不是中标概率。"""
    reasons: list[str] = []
    risks: list[str] = []
    title = (opportunity.title or "").casefold()
    region = (opportunity.region or "").casefold()
    industries = set(opportunity.industry or [])
    budget = opportunity.budget_amount
    hit_any = False
    for preference in preferences:
        matched: list[str] = []
        keywords = [item for item in preference.keywords if item.casefold() in title]
        if keywords:
            matched.append("关键词 " + "、".join(keywords[:3]))
        regions = [item for item in preference.regions if item.casefold() in region]
        if regions:
            matched.append("地区 " + "、".join(regions[:2]))
        industry_hits = [item for item in preference.industries if item in industries]
        if industry_hits:
            matched.append("行业 " + "、".join(industry_hits[:2]))
        if budget is not None and preference.budget_contains(budget):
            matched.append("预算区间")
        if matched:
            hit_any = True
            reasons.append("命中订阅：" + "；".join(matched))
    if buyer_watched:
        reasons.append("采购人在关注清单")
    if buyer_intel:
        recent = int(buyer_intel.get("recent_wins_12m") or 0)
        suppliers = buyer_intel.get("existing_suppliers") or []
        if recent:
            reasons.append(f"采购人近 12 月中标 {recent} 次")
        if suppliers:
            reasons.append(f"已识别既有供应商 {len(suppliers)} 家")
    if not preferences:
        risks.append("未配置订阅，当前为默认排序")
    elif not hit_any:
        risks.append("未命中当前启用订阅，需人工判断")
    if budget is None:
        risks.append("预算金额缺失")
    if opportunity.bid_deadline is not None and opportunity.bid_deadline < datetime.now(UTC):
        risks.append("投标截止已过")
    return {
        "reasons": reasons[:8],
        "risks": risks[:8],
        "buyer_watched": buyer_watched,
        "buyer_intel": buyer_intel,
    }


def hit_groups(preferences: list[MatchPreference], opportunity: Opportunity) -> list[str]:
    """返回命中的订阅条件组（关键词/地区/行业/预算）。

    与 match_score_expression 的启发式加权同源，仅描述命中了哪类订阅条件，
    不是模型评分，也不代表中标概率。
    """
    title = (opportunity.title or "").casefold()
    region = (opportunity.region or "").casefold()
    industries = set(opportunity.industry or [])
    budget = opportunity.budget_amount
    groups: set[str] = set()
    for preference in preferences:
        if preference.keywords and any(
            keyword.casefold() in title for keyword in preference.keywords
        ):
            groups.add("keyword")
        if preference.regions and any(item.casefold() in region for item in preference.regions):
            groups.add("region")
        if preference.industries and set(preference.industries) & industries:
            groups.add("industry")
        if budget is not None and preference.budget_contains(budget):
            groups.add("budget")
    return [name for name in ("keyword", "region", "industry", "budget") if name in groups]
