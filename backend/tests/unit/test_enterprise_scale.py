"""中小企业划型：《中小企业划型标准规定》（工信部联企业〔2011〕300 号）。

为什么这个判定要求这么严：
- 政府采购「专门面向中小企业」的项目，声明函填错**即废标**，且可能被认定为
  提供虚假材料；
- 划型看行业 + 从业人员 + 营业收入/资产总额，不同行业阈值差别极大；
- 很多企业按「感觉自己不大」自认小微，一核对就超了。
"""

from decimal import Decimal

import pytest

from app.modules.assets.enterprise_scale import (
    classify_enterprise,
    is_sme,
    parse_employees,
    resolve_industry,
)

D = Decimal


@pytest.mark.parametrize(
    ("industry", "employees", "revenue", "expected"),
    [
        # 软件和信息技术服务业：中型 100人&1000万 / 小型 10人&50万 / 大型 300人&1亿
        ("软件和信息技术服务业", 120, D("2000"), "中型"),
        ("软件和信息技术服务业", 50, D("600"), "小型"),
        ("软件和信息技术服务业", 5, D("30"), "微型"),
        ("软件和信息技术服务业", 500, D("20000"), "大型"),
        # 物业管理：中型 300人&1000万
        ("物业管理", 400, D("1500"), "中型"),
        ("物业管理", 150, D("600"), "小型"),
        # 批发业：中型 20人&5000万（人少但营收高也算中型）
        ("批发业", 25, D("6000"), "中型"),
        ("批发业", 10, D("2000"), "小型"),
        # 工业：大型 1000人&4亿
        ("制造业", 800, D("30000"), "中型"),
        ("制造业", 1200, D("50000"), "大型"),
        # 工业小型需 20 人且 300 万，15 人属微型
        ("制造业", 25, D("400"), "小型"),
        ("制造业", 15, D("200"), "微型"),
        # 零售业：中型 50人&500万
        ("零售业", 60, D("800"), "中型"),
    ],
)
def test_classify_by_industry(
    industry: str, employees: int, revenue: Decimal, expected: str
) -> None:
    result = classify_enterprise(industry=industry, employees=employees, revenue=revenue)
    assert result.scale == expected, result.basis


def test_construction_uses_total_assets_not_revenue() -> None:
    """建筑业按「营业收入 + 资产总额」双指标，不是单看营收。"""
    result = classify_enterprise(
        industry="建筑业", revenue=D("7000"), total_assets=D("6000")
    )
    assert result.scale == "中型"
    assert "资产总额" in result.basis


def test_missing_metric_returns_none_not_guess() -> None:
    """缺营业收入时必须拒判，不能假定为 0——假定为 0 会把中型企业误判成微型。"""
    result = classify_enterprise(industry="软件和信息技术服务业", employees=100)
    assert result.scale is None
    assert result.missing == ["营业收入"]


def test_construction_missing_assets_returns_none() -> None:
    result = classify_enterprise(industry="建筑业", revenue=D("7000"))
    assert result.scale is None
    assert "资产总额" in result.missing


def test_unknown_industry_returns_none() -> None:
    result = classify_enterprise(industry="航天器维修", employees=100, revenue=D("1000"))
    assert result.scale is None
    assert result.missing == ["行业"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("软件和信息技术服务业", "software"),
        ("信息技术服务", "software"),
        ("系统集成与运维", "software"),
        ("建筑工程施工", "construction"),
        ("物业管理服务", "property"),
        ("道路运输", "transport"),
        ("货物批发", "wholesale"),
        ("餐饮服务", "catering"),
    ],
)
def test_resolve_industry_aliases(text: str, expected: str) -> None:
    assert resolve_industry(text) == expected


def test_is_sme_boundary() -> None:
    assert is_sme("中型") is True
    assert is_sme("小型") is True
    assert is_sme("微型") is True
    assert is_sme("大型") is False
    assert is_sme(None) is None


def test_parse_employees_variants() -> None:
    assert parse_employees("120人") == 120
    assert parse_employees("从业人员 85 名") == 85
    assert parse_employees("300") == 300
    assert parse_employees("未披露") is None


def test_basis_is_human_readable() -> None:
    """依据要能被复核：带上指标数值，用户才知道系统凭什么这么判。"""
    result = classify_enterprise(
        industry="软件和信息技术服务业", employees=120, revenue=D("2000")
    )
    assert "120" in result.basis
    assert "2000" in result.basis


@pytest.mark.parametrize("key", ["software", "construction", "property", "wholesale"])
def test_resolve_industry_accepts_standard_key(key: str) -> None:
    """前端下拉框传的是标准 key，必须能识别。

    早期只做中文别名匹配，导致「传 software 却说无法判定行业」——
    这是端到端调用才暴露的问题，接口层传什么形状必须都吃得住。
    """
    assert resolve_industry(key) == key


@pytest.mark.parametrize("label", ["软件和信息技术服务业", "建筑业", "物业管理"])
def test_resolve_industry_accepts_standard_label(label: str) -> None:
    assert resolve_industry(label) is not None


def test_classify_with_industry_key() -> None:
    result = classify_enterprise(industry="software", employees=500, revenue=D("20000"))
    assert result.scale == "大型"
    assert result.industry == "software"
