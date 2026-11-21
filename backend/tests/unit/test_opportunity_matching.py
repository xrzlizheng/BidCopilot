from decimal import Decimal

from app.core.like import like_contains
from app.modules.opportunities.matching import (
    MatchPreference,
    count_match_hits,
    preference_from_query,
    preferences_from_subscriptions,
)
from app.modules.opportunities.models import Opportunity, OpportunitySubscription


def _opportunity(**overrides) -> Opportunity:
    values = {
        "title": "某市政务云运维项目",
        "region": "广东 广州市",
        "industry": ["政府采购"],
        "budget_amount": Decimal("5000000"),
    }
    values.update(overrides)
    return Opportunity(**values)


def test_budget_ranges_are_evaluated_per_subscription_not_as_envelope() -> None:
    preferences = [
        MatchPreference(budget_min=Decimal("1000000"), budget_max=Decimal("2000000")),
        MatchPreference(budget_min=Decimal("10000000"), budget_max=Decimal("20000000")),
    ]
    # 5M 落在两个订阅区间之间：旧实现用 min/max 包络会误判命中。
    assert count_match_hits(preferences, _opportunity()) == 0
    inside = _opportunity(budget_amount=Decimal("1500000"))
    assert count_match_hits(preferences, inside) == 1


def test_keyword_and_region_matching_is_case_insensitive() -> None:
    preference = preference_from_query({"keywords": ["ABC"], "region_list": [{"province": "广东"}]})
    opportunity = _opportunity(title="abc 数据中心项目", region="广东 深圳市")
    assert count_match_hits([preference], opportunity) == 2
    assert count_match_hits([preference], _opportunity(title="其他项目", region="江苏")) == 0


def test_budget_open_ended_bounds() -> None:
    lower_only = MatchPreference(budget_min=Decimal("5000000"))
    assert lower_only.budget_contains(Decimal("5000000")) is True
    assert lower_only.budget_contains(Decimal("4999999")) is False
    upper_only = MatchPreference(budget_max=Decimal("5000000"))
    assert upper_only.budget_contains(Decimal("5000000")) is True
    assert upper_only.budget_contains(Decimal("5000001")) is False
    assert MatchPreference().budget_contains(Decimal("1")) is False


def test_preferences_skip_subscriptions_without_conditions() -> None:
    empty = OpportunitySubscription(
        tenant_id=None,  # type: ignore[arg-type]
        user_id=None,  # type: ignore[arg-type]
        name="全量",
        query_json={"keywords": [], "page_size": 20},
    )
    configured = OpportunitySubscription(
        tenant_id=None,  # type: ignore[arg-type]
        user_id=None,  # type: ignore[arg-type]
        name="有偏好",
        query_json={"keywords": ["政务云"]},
    )
    preferences = preferences_from_subscriptions([empty, configured])
    assert len(preferences) == 1
    assert preferences[0].keywords == ("政务云",)


def test_like_contains_treats_user_keywords_as_literals() -> None:
    assert like_contains("50%") == "%50\\%%"
    assert like_contains("a_b") == "%a\\_b%"
    assert like_contains("a\\b") == "%a\\\\b%"
