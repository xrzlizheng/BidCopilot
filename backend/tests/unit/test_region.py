"""地区补全：公告上游常只给市/区县，甚至全省略。

补全的价值不在「好看」，而在：
- 地区筛选用 `region ilike '%安徽%'`，只写「合肥市」的记录搜「安徽」永远搜不到；
- 按省统计时这些记录会掉进「未知」。
原则是宁可少补，不可补错——补错地区比留空更糟。
"""

import pytest

from app.core.region import normalize_region, region_from_text


@pytest.mark.parametrize(
    ("title", "buyer", "expected"),
    [
        # 标题里带完整市名
        ("武汉市医疗保障局2026年政府采购意向", None, "湖北省 武汉市"),
        ("南宁职业技术大学北斗测绘应用实训中心", None, "广西壮族自治区 南宁市"),
        ("临汾广播电视台应急广播项目", None, "山西省 临汾市"),
        ("银川市金凤区智慧城市建设项目", None, "宁夏回族自治区 银川市 金凤区"),
        # 只有省
        ("河北省人社厅数字人社建设项目", None, "河北省"),
        # 采购人信息补充
        (None, "合肥市数据资源局", "安徽省 合肥市"),
        # 直辖市
        ("北京市朝阳区政务云运维", None, "北京市 北京市 朝阳区"),
    ],
)
def test_region_from_text_completes_province(
    title: str | None, buyer: str | None, expected: str
) -> None:
    assert region_from_text(title, buyer) == expected


@pytest.mark.parametrize(
    "value",
    ["城区", "开发区", "高新区", "推进剂浇注数字化安全管控单元"],
)
def test_region_from_text_never_guesses(value: str) -> None:
    """无法唯一确定省份时必须返回 None，不能瞎填一个省。"""
    assert region_from_text(value) is None


def test_normalize_region_removes_duplicate() -> None:
    """「北京 丰台区 丰台区」是上游 province/city/county 同值导致的脏数据。"""
    assert normalize_region("北京 丰台区 丰台区") == "北京市 北京市 丰台区"


def test_normalize_region_adds_missing_province() -> None:
    assert normalize_region("湖北 武汉市") == "湖北省 武汉市"
    assert normalize_region("江苏 无锡市 梁溪区") == "江苏省 无锡市 梁溪区"


def test_normalize_region_keeps_autonomous_region_full_name() -> None:
    assert normalize_region("内蒙古 乌兰察布市 集宁区") == "内蒙古自治区 乌兰察布市 集宁区"


def test_normalize_region_handles_empty() -> None:
    assert normalize_region(None) is None
    assert normalize_region("   ") is None


def test_normalize_region_recovers_province_from_unique_county() -> None:
    """官方区划表里全国唯一的区县，可以直接反推省市（梁溪区只在无锡）。"""
    assert normalize_region("梁溪区") == "江苏省 无锡市 梁溪区"


def test_normalize_region_keeps_ambiguous_county_as_is() -> None:
    """跨省市重名的区县（朝阳区在北京/长春）无法反推，保留原值不猜。"""
    assert normalize_region("朝阳区") == "朝阳区"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # 区县唯一 -> 直连到地级市
        ("武乡县公安局交通卡口", "山西省 长治市 武乡县"),
        # 无「市」后缀的地级市
        ("河北石家庄新华区", "河北省 石家庄市 新华区"),
        ("广西南宁职业技术大学", "广西壮族自治区 南宁市"),
        # 重名区县靠已知上级消歧
        ("北京市朝阳区数据平台", "北京市 北京市 朝阳区"),
        ("长春市朝阳区政务云", "吉林省 长春市 朝阳区"),
    ],
)
def test_region_inference_uses_official_divisions(text: str, expected: str) -> None:
    assert region_from_text(text) == expected


@pytest.mark.parametrize("text", ["中山路改造工程", "韩山师范学院", "高新区智慧园区项目"])
def test_region_inference_rejects_unresolvable(text: str) -> None:
    """路名/校名/泛称无法定位省份时必须返回 None。"""
    assert region_from_text(text) is None


def test_region_confidence_levels() -> None:
    from app.core.region import region_confidence

    assert region_confidence("安徽省 合肥市 蜀山区") == "exact"
    assert region_confidence("湖北省 武汉市") == "partial"
    assert region_confidence("河北省") == "inferred"
    assert region_confidence(None) == "none"


def test_address_gaps_reports_missing_levels() -> None:
    from app.core.region import address_gaps

    assert address_gaps("安徽省 合肥市 蜀山区") == []
    assert address_gaps("湖北省 武汉市") == ["区/县"]
    assert address_gaps(None) == ["省", "市", "区/县"]


@pytest.mark.parametrize(
    "text",
    [
        "中山路改造工程",          # 「中山」是市也是路名
        "东城区政务服务中心",      # 直辖市辖区，但不能反推省份
        "高新区智慧园区项目",      # 泛称
        "南京东路街道",            # 「南京」是上海的街道名
    ],
)
def test_region_inference_avoids_false_positive(text: str) -> None:
    """常见误伤：路名/街道名里的城市名不能被当成项目所在地。"""
    region = region_from_text(text)
    assert region is None or "中山市" not in region


@pytest.mark.parametrize(
    ("value", "exact", "term"),
    [
        # 省名：无歧义，可前缀精确匹配
        ("山西", True, "山西省"),
        ("广东", True, "广东省"),
        ("内蒙古", True, "内蒙古自治区"),
        # 带「市」的地级市：明确，可精确
        ("合肥市", True, "合肥市"),
        ("北京市", True, "北京市"),
        # 全国唯一的区县：可精确到省市县
        ("梁溪区", True, "江苏省 无锡市 梁溪区"),
        # 有歧义的裸名：必须退回模糊，否则漏结果
        ("朝阳", False, "朝阳"),
        ("朝阳区", False, "朝阳区"),
        ("合肥", False, "合肥"),
        ("南", False, "南"),
    ],
)
def test_filter_terms_avoids_false_narrowing(value: str, exact: bool, term: str) -> None:
    """筛选不能把有歧义的输入强行归一化。

    「朝阳」可能是北京朝阳区或辽宁朝阳市；强行变成「辽宁省 朝阳市」
    会让搜北京朝阳的用户一条都查不到——宁可多返回，不能漏返回。
    """
    from app.core.region import filter_terms

    assert filter_terms(value) == (exact, term)


def test_filter_terms_empty() -> None:
    from app.core.region import filter_terms

    assert filter_terms(None) == (False, "")
    assert filter_terms("  ") == (False, "")
