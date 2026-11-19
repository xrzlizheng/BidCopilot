from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from app.core.errors import ValidationAppError
from app.modules.opportunities.auto_match import (
    DEFAULT_AUTO_MATCH_SETTINGS,
    CompanyCapability,
    decide_match,
    detect_quantity,
    detect_required_level,
    evaluate_company_qualifications,
    evaluate_opportunity_match,
    extract_qualification_requirements,
    project_creation_plan,
    requirements_from_constraint,
    select_best_company_qualification,
    validate_auto_match_settings,
)
from app.modules.opportunities.matching import MatchPreference, count_match_hits, hit_groups
from app.modules.opportunities.models import Opportunity


def _opportunity(**overrides) -> Opportunity:
    values = {
        "title": "某市政务数据中心集成项目",
        "region": "广东 广州",
        "industry": ["政务"],
        "budget_amount": Decimal("5000000"),
        "content_text": None,
    }
    values.update(overrides)
    return Opportunity(**values)


def _capability(**overrides) -> CompanyCapability:
    values = {
        "company_id": uuid4(),
        "company_name": "示例科技有限公司",
        "region": "广东 广州",
        "industry": "政务",
        "qualifications": (),
        "personnel": (),
        "performances": (),
        "credits": (),
    }
    values.update(overrides)
    return CompanyCapability(**values)


def _qualification_requirement(text: str, level: str | None = None) -> dict:
    return {"text": text, "category": "QUALIFICATION", "level": level, "quantity": None}


CONTENT = """第一章 招标公告
二、申请人的资格要求：
1. 具有信息系统集成及服务资质二级及以上；
2. 拟派项目经理具备PMP证书；
3. 近三年具有不少于2项类似项目业绩；
4. 未被列入失信被执行人名单。
三、评标办法
综合评分法。
"""


def test_extract_qualification_requirements_from_explicit_section() -> None:
    requirements = extract_qualification_requirements(CONTENT)
    assert [item["category"] for item in requirements] == [
        "QUALIFICATION",
        "PERSONNEL",
        "PERFORMANCE",
        "CREDIT",
    ]
    assert requirements[0]["level"] == "二级"
    assert requirements[2]["quantity"] == 2
    assert all(item["source_span"].startswith("line:") for item in requirements)


def test_extract_returns_empty_when_section_missing_or_no_content() -> None:
    assert extract_qualification_requirements(None) == []
    assert extract_qualification_requirements("") == []
    assert extract_qualification_requirements("某市政务数据中心项目，预算五百万元。") == []


def test_constraint_payload_requirements_are_normalized() -> None:
    payload = {
        "qualification_requirements": [
            {
                "text": "具有信息系统集成及服务资质二级及以上",
                "category": "QUALIFICATION",
                "level": "二级",
            },
            {"name": "近三年不少于2项类似项目业绩"},
            {"text": "具备独立承担民事责任的能力", "category": "OTHER"},
        ]
    }
    requirements = requirements_from_constraint(payload)
    assert [item["category"] for item in requirements] == [
        "QUALIFICATION",
        "PERFORMANCE",
        "OTHER",
    ]
    assert requirements[0]["level"] == "二级"
    assert requirements[1]["quantity"] == 2
    assert requirements_from_constraint(None) == []
    assert requirements_from_constraint({"qualification_requirements": "bad"}) == []


def test_level_and_quantity_detection() -> None:
    assert detect_required_level("具备甲级资质") == "甲级"
    assert detect_required_level("等级不低于三级") == "三级"
    assert detect_required_level("无等级要求") is None
    assert detect_quantity("不少于3个类似项目") == 3
    assert detect_quantity("提供 2 份合同") == 2
    assert detect_quantity("无数量要求") is None


def test_qualification_pass_when_cert_and_level_match() -> None:
    capability = _capability(
        qualifications=(
            {
                "id": uuid4(),
                "name": "信息系统集成及服务资质",
                "level": "二级",
                "valid_to": date(2030, 1, 1),
            },
        )
    )
    requirement = _qualification_requirement("具有信息系统集成及服务资质二级及以上", "二级")
    result = evaluate_company_qualifications([requirement], capability, date(2026, 1, 1))
    assert result["qualified"] is True
    assert result["pass_count"] == 1
    assert result["gaps"] == []


def test_qualification_missing_expired_and_level_insufficient() -> None:
    missing = evaluate_company_qualifications(
        [_qualification_requirement("具有信息系统集成及服务资质")],
        _capability(),
        date(2026, 1, 1),
    )
    assert missing["qualified"] is False
    assert missing["gaps"][0]["status"] == "MISSING"

    expired = evaluate_company_qualifications(
        [_qualification_requirement("具有信息系统集成资质")],
        _capability(
            qualifications=(
                {
                    "id": uuid4(),
                    "name": "信息系统集成资质",
                    "level": None,
                    "valid_to": date(2020, 1, 1),
                },
            )
        ),
        date(2026, 1, 1),
    )
    assert expired["gaps"][0]["status"] == "FAIL"
    assert "过期" in expired["gaps"][0]["reason"]

    insufficient = evaluate_company_qualifications(
        [_qualification_requirement("具有信息系统集成资质二级及以上", "二级")],
        _capability(
            qualifications=(
                {
                    "id": uuid4(),
                    "name": "信息系统集成资质",
                    "level": "三级",
                    "valid_to": date(2030, 1, 1),
                },
            )
        ),
        date(2026, 1, 1),
    )
    assert insufficient["gaps"][0]["status"] == "FAIL"
    assert "等级不足" in insufficient["gaps"][0]["reason"]


def test_best_company_is_selected_and_no_requirements_is_unknown() -> None:
    requirement = _qualification_requirement("具有信息系统集成资质二级及以上", "二级")
    passing = _capability(
        qualifications=(
            {
                "id": uuid4(),
                "name": "信息系统集成资质",
                "level": "二级",
                "valid_to": date(2030, 1, 1),
            },
        )
    )
    failing = _capability()
    chosen = select_best_company_qualification([requirement], [failing, passing], date(2026, 1, 1))
    assert chosen["qualified"] is True
    assert chosen["company_id"] == passing.company_id

    unknown = select_best_company_qualification([], [passing], date(2026, 1, 1))
    assert unknown["qualified"] is None
    assert unknown["gaps"][0]["status"] == "UNKNOWN"


def test_unknown_deadline_does_not_assume_validity_coverage() -> None:
    requirement = _qualification_requirement("信息系统集成及服务资质", "二级")
    capability = _capability(
        qualifications=(
            {
                "id": str(uuid4()),
                "name": "信息系统集成及服务资质",
                "level": "二级",
                "valid_to": "2030-01-01",
            },
        )
    )
    chosen = select_best_company_qualification([requirement], [capability], None)
    assert chosen["qualified"] is None
    assert chosen["gaps"][0]["status"] == "UNKNOWN"
    assert "截止日未知" in chosen["gaps"][0]["reason"]


def test_other_requirement_type_is_unknown_not_guessed() -> None:
    requirement = {
        "text": "具有独立承担民事责任的能力",
        "category": "OTHER",
        "level": None,
        "quantity": None,
    }
    result = evaluate_company_qualifications([requirement], _capability(), date(2026, 1, 1))
    assert result["qualified"] is None
    assert result["gaps"][0]["status"] == "UNKNOWN"


def _qualification_state(*, qualified: bool | None, passed: int, total: int) -> dict:
    return {
        "qualified": qualified,
        "pass_count": passed,
        "total": total,
        "gaps": [{"status": "MISSING"}] if passed < total else [],
    }


def test_heuristic_score_reuses_subscription_groups_and_capability() -> None:
    opportunity = _opportunity()
    preferences = [
        MatchPreference(
            keywords=("数据中心",),
            regions=("广东",),
            industries=("政务",),
            budget_min=Decimal("1000000"),
            budget_max=Decimal("10000000"),
        )
    ]
    assert count_match_hits(preferences, opportunity) == 4
    assert hit_groups(preferences, opportunity) == [
        "keyword",
        "region",
        "industry",
        "budget",
    ]
    evaluation = evaluate_opportunity_match(
        opportunity,
        preferences,
        _qualification_state(qualified=True, passed=1, total=1),
        [_capability()],
    )
    assert evaluation["match_score"] == 100
    assert "qualification" in evaluation["hit_groups"]
    assert "company_industry" in evaluation["hit_groups"]


def test_decision_threshold_branches() -> None:
    settings = dict(DEFAULT_AUTO_MATCH_SETTINGS)
    assert decide_match(True, 60, settings) == "recommend"
    assert decide_match(True, 59, settings) == "review"
    assert decide_match(None, 90, settings) == "review"
    assert decide_match(False, 20, settings) == "skip"
    assert decide_match(False, 40, settings) == "review"


def test_unknown_qualification_never_recommends_even_with_high_score() -> None:
    evaluation = evaluate_opportunity_match(
        _opportunity(),
        [MatchPreference(keywords=("数据中心",))],
        _qualification_state(qualified=None, passed=0, total=1),
        [_capability()],
    )
    assert evaluation["match_score"] == 40
    assert decide_match(None, evaluation["match_score"], dict(DEFAULT_AUTO_MATCH_SETTINGS)) == "review"
    assert any("人工复核" in risk for risk in evaluation["risks"])


def test_auto_match_settings_defaults_and_validation() -> None:
    defaults = validate_auto_match_settings({})
    assert defaults == {
        "enabled": True,
        "recommend_min_score": 60,
        "skip_below_score": 30,
        "auto_create_project": False,
        "notify": True,
    }
    updated = validate_auto_match_settings(
        {"recommend_min_score": 80, "skip_below_score": 50, "auto_create_project": True},
        base=defaults,
    )
    assert updated["recommend_min_score"] == 80
    assert updated["auto_create_project"] is True
    with pytest.raises(ValidationAppError):
        validate_auto_match_settings({"recommend_min_score": 10, "skip_below_score": 20})
    with pytest.raises(ValidationAppError):
        validate_auto_match_settings({"recommend_min_score": 101})


def test_project_creation_is_idempotent_across_repeat_and_concurrent_calls() -> None:
    existing_id = uuid4()
    assert (
        project_creation_plan(
            decision="recommend",
            auto_create_project=True,
            existing_project_id=existing_id,
            lock_acquired=True,
        )
        == "existing"
    )
    assert (
        project_creation_plan(
            decision="recommend",
            auto_create_project=True,
            existing_project_id=None,
            lock_acquired=False,
        )
        == "concurrent"
    )
    assert (
        project_creation_plan(
            decision="recommend",
            auto_create_project=True,
            existing_project_id=None,
            lock_acquired=True,
        )
        == "create"
    )
    assert (
        project_creation_plan(
            decision="review",
            auto_create_project=True,
            existing_project_id=None,
            lock_acquired=True,
        )
        == "skip"
    )
    assert (
        project_creation_plan(
            decision="recommend",
            auto_create_project=False,
            existing_project_id=None,
            lock_acquired=True,
        )
        == "skip"
    )
