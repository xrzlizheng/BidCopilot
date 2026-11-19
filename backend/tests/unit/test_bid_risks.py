"""投标风险画像：招投标实操里最常踩的废标点。

这些规则的价值不在「识别到资质要求」（那太宽），而在识别**差一点就被废**的门槛：
近三年不少于 2 个业绩、单个合同≥500 万、不接受联合体、中小企业声明函缺失即废……
"""

from types import SimpleNamespace

import pytest

from app.modules.tender.bid_risks import extract_bid_risks


def _blocks(*texts: str) -> list[SimpleNamespace]:
    return [
        SimpleNamespace(
            text=text, index=index, table_index=None, row=None, col=None, kind="paragraph"
        )
        for index, text in enumerate(texts)
    ]


def test_performance_threshold_extracts_years_and_count() -> None:
    """「近三年不少于 2 个」两个约束必须都抽出来——只记一个会导致误判可投。"""
    result = extract_bid_risks(_blocks("投标人须具有近三年不少于 2 个类似项目业绩。"))
    item = next(i for i in result["items"] if i["code"] == "performance_threshold")
    assert item["years"] == 3
    assert item["min_count"] == 2
    assert item["level"] == "blocker"


def test_performance_threshold_supports_chinese_numerals() -> None:
    result = extract_bid_risks(_blocks("近五年至少三个同类业绩。"))
    item = next(i for i in result["items"] if i["code"] == "performance_threshold")
    assert (item["years"], item["min_count"]) == (5, 3)


def test_performance_amount_normalized_to_yuan() -> None:
    result = extract_bid_risks(_blocks("单个合同金额不低于 500 万元。"))
    item = next(i for i in result["items"] if i["code"] == "performance_amount")
    assert item["min_amount"] == 5_000_000


def test_consortium_rejection_is_blocker_and_clears_flag() -> None:
    result = extract_bid_risks(_blocks("本项目不接受联合体投标。"))
    assert result["flags"]["consortium_allowed"] is False
    assert any(i["code"] == "consortium_rejected" for i in result["items"])


def test_consortium_allowed_flag_when_explicitly_permitted() -> None:
    result = extract_bid_risks(_blocks("本项目接受联合体投标。"))
    assert result["flags"]["consortium_allowed"] is True


def test_sme_declaration_is_blocker() -> None:
    """中小项目里声明函缺失是废标，不是扣分——等级必须是 blocker。"""
    result = extract_bid_risks(
        _blocks("本项目专门面向中小企业，投标人须提供中小企业声明函。")
    )
    codes = {item["code"]: item["level"] for item in result["items"]}
    assert codes["sme_declaration"] == "blocker"
    assert codes["sme_only_scope"] == "high"
    assert result["flags"]["sme_only"] is True


def test_branch_allowance_detected() -> None:
    result = extract_bid_risks(_blocks("分公司可使用总公司资质参与投标。"))
    assert result["flags"]["branch_allowed"] is True


def test_original_document_requirement() -> None:
    result = extract_bid_risks(_blocks("开标时须携带营业执照原件核验。"))
    assert result["flags"]["original_required"] is True


def test_local_requirement_detected() -> None:
    result = extract_bid_risks(_blocks("投标人须在本市注册并缴纳社保满一年。"))
    assert any(i["code"] == "local_requirement" for i in result["items"])


def test_affiliate_and_agency_conflict_are_blockers() -> None:
    result = extract_bid_risks(
        _blocks(
            "与本项目提供设计、咨询服务的单位不得参与投标。",
            "同一集团下属企业不得同时投标。",
        )
    )
    levels = {item["code"]: item["level"] for item in result["items"]}
    assert levels["agency_conflict"] == "blocker"
    assert levels["affiliate_bidding"] == "blocker"


def test_no_false_positive_on_plain_text() -> None:
    result = extract_bid_risks(_blocks("本项目采购内容为政务云运维服务。"))
    assert result["items"] == []
    assert result["summary"]["verdict"] == "未识别到硬性门槛"


def test_summary_counts_by_level() -> None:
    result = extract_bid_risks(
        _blocks(
            "本项目不接受联合体投标。",
            "近三年不少于 2 个类似项目业绩。",
            "开标时须携带原件。",
        )
    )
    summary = result["summary"]
    assert summary["blocker"] == 2
    assert summary["high"] == 1
    assert summary["total"] == 3


@pytest.mark.parametrize(
    ("text", "expected_code"),
    [
        ("近三年内具有不少于 3 项同类业绩", "performance_threshold"),
        ("单项合同金额不低于 300 万元", "performance_amount"),
        ("不允许联合体投标", "consortium_rejected"),
        ("投标人须提供营业执照原件", "original_document"),
    ],
)
def test_rule_variants(text: str, expected_code: str) -> None:
    result = extract_bid_risks(_blocks(text))
    assert any(item["code"] == expected_code for item in result["items"]), result["items"]


def test_credit_query_screenshot_is_blocker() -> None:
    """信用记录干净不等于证明过程合规：漏截图/时间不符/未盖章都会被形式审查废标。"""
    result = extract_bid_risks(
        _blocks("投标人须提供「信用中国」网站查询截图并加盖公章，查询时间须在公告发布日之后。")
    )
    item = next(i for i in result["items"] if i["code"] == "credit_query_evidence")
    assert item["level"] == "blocker"
    assert result["flags"]["credit_query_required"] is True


def test_credit_query_evidence_supports_three_official_sites() -> None:
    result = extract_bid_risks(
        _blocks(
            "提供中国政府采购网查询结果截图及国家企业信用信息公示系统网页打印件。",
            "须提供信用查询截图，且查询时间须在投标截止日前。",
        )
    )
    assert any(i["code"] == "credit_query_evidence" for i in result["items"])


def test_plain_credit_site_mention_is_not_a_requirement() -> None:
    """只是介绍信用中国是官方渠道，不应误报为必须提交截图。"""
    result = extract_bid_risks(_blocks("信用中国网站是查询信用信息的官方渠道。"))
    assert all(i["code"] != "credit_query_evidence" for i in result["items"])
    assert result["flags"]["credit_query_required"] is False


def test_must_bring_derived_from_blockers() -> None:
    """风险点要能派生成开标必带材料——只有风险标签，用户不知道带什么。"""
    result = extract_bid_risks(
        _blocks(
            "本项目专门面向中小企业，须提供中小企业声明函。",
            "投标人须具有近三年不少于 2 个类似项目业绩。",
        )
    )
    items = {row["code"]: row for row in result["must_bring"]}
    assert "sme_declaration" in items
    assert "中小企业声明函" in items["sme_declaration"]["item"]
    assert items["sme_declaration"]["critical"] is True
    assert "业绩合同" in items["performance_threshold"]["item"]


def test_must_bring_puts_critical_first() -> None:
    result = extract_bid_risks(
        _blocks(
            "开标时须携带营业执照原件核验。",           # critical
            "同一集团下属企业不得同时投标。",           # not critical
        )
    )
    critical_flags = [row["critical"] for row in result["must_bring"]]
    assert critical_flags == sorted(critical_flags, reverse=True)


def test_must_bring_empty_when_no_risk() -> None:
    result = extract_bid_risks(_blocks("本项目采购内容为政务云运维服务。"))
    assert result["must_bring"] == []


def test_must_bring_includes_original_document_from_flag() -> None:
    """原件核验有时只在证照清单命中，风险规则未必命中，用 flag 兜底补上。"""
    result = extract_bid_risks(_blocks("投标人须提供营业执照原件。"))
    codes = {row["code"] for row in result["must_bring"]}
    assert "original_document" in codes
