from datetime import date

from app.modules.projects.matching import (
    legacy_decision_payload,
    match_scoring_item,
    qualification_matches,
    shares_keyword,
)

ASSETS = {
    "qualifications": [
        {"id": "q1", "name": "涉密信息系统集成", "level": "乙级", "valid_to": "2027-01-01"},
        {"id": "q2", "name": "信息安全等级保护", "level": "三级", "valid_to": "2020-01-01"},
    ],
    "personnel": [
        {"id": "p1", "name": "张三", "cert_type": "PMP", "valid_to": "2027-01-01"},
    ],
    "performances": [
        {"id": "f1", "name": "政务云系统集成项目"},
        {"id": "f2", "name": "数据平台建设项目"},
    ],
    "credits": [
        {"id": "c1", "name": "信用记录", "status": "正常"},
    ],
}


def test_shares_keyword() -> None:
    assert shares_keyword("涉密信息系统集成", "投标人须具备涉密信息系统集成乙级资质")
    assert shares_keyword("PMP", "项目经理须具备 PMP 证书")
    assert not shares_keyword("建筑工程", "信息安全等级保护")


def test_matrix_covered_and_missing() -> None:
    covered = match_scoring_item("项目经理（PMP）", "持证人员配置", ASSETS)
    assert covered["coverage_status"] == "covered"
    assert covered["matched_assets"][0]["id"] == "p1"

    missing = match_scoring_item("项目经理（PMP）", "持证人员配置", {"personnel": []})
    assert missing["coverage_status"] == "missing"
    assert missing["missing_evidence"] == ["人员"]


def test_qualification_pass_fail_unknown() -> None:
    requirements = [
        {"category": "QUALIFICATION", "text": "投标人须具备涉密信息系统集成乙级资质", "level": "乙级"},
        {"category": "QUALIFICATION", "text": "投标人须具备信息安全等级保护三级资质", "level": "三级"},
        {"category": "QUALIFICATION", "text": "投标人须具备 CMMI 五级认证", "level": "五级"},
        {"category": "PERSONNEL", "text": "项目经理须具备 PMP 证书"},
    ]

    results = qualification_matches(requirements, ASSETS, date(2026, 11, 10))

    assert results[0]["status"] == "PASS"
    assert results[1]["status"] == "FAIL"
    assert results[1]["reason"] == "证书已过期"
    assert results[2]["status"] == "UNKNOWN"
    assert results[3]["status"] == "PASS"


def test_performance_quantity() -> None:
    requirements = [{"category": "PERFORMANCE", "text": "近三年具有不少于 2 个类似项目业绩", "quantity": 2}]

    results = qualification_matches(requirements, ASSETS, date(2026, 11, 10))
    assert results[0]["status"] == "PASS"

    results = qualification_matches(requirements, {**ASSETS, "performances": ASSETS["performances"][:1]}, date(2026, 11, 10))
    assert results[0]["status"] == "FAIL"


def test_decision_levels() -> None:
    go = legacy_decision_payload(
        [{"status": "PASS"}, {"status": "PASS"}],
        [{"coverage_status": "covered", "missing_evidence": []}],
    )
    assert go["status"] == "Go"

    conditional = legacy_decision_payload(
        [{"status": "PASS"}, {"status": "UNKNOWN"}],
        [{"coverage_status": "partial", "missing_evidence": ["人员"]}],
    )
    assert conditional["status"] == "Conditional Go"

    no_go = legacy_decision_payload(
        [{"status": "FAIL"}],
        [{"coverage_status": "covered", "missing_evidence": []}],
    )
    assert no_go["status"] == "No-Go"
