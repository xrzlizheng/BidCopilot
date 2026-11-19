"""合同审查纯逻辑单测：条款切分/分类、风险规则命中、总价偏离（无 DB）。"""

import pytest

from app.modules.contracts.service import (
    apply_risk_rules,
    classify_clause,
    parse_amount_text,
    split_clauses,
    tri_party_consistency,
)
from app.modules.tender.parser import Block, ParsedDocument


def _parsed(*lines: str) -> ParsedDocument:
    return ParsedDocument(
        blocks=[Block(index=i, text=text, kind="paragraph", page=1) for i, text in enumerate(lines)]
    )


def _clauses(*texts: str) -> list[dict]:
    return [
        {"seq": i + 1, "title": text[:20], "text": text, "category": classify_clause(text), "page": 1}
        for i, text in enumerate(texts)
    ]


def test_parse_amount_text_units() -> None:
    assert parse_amount_text("合同总价为人民币 210 万元。") == 2100000
    assert parse_amount_text("总价 1,200,500 元") == 1200500


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("合同总价为人民币贰佰万元整。", 2000000),
        ("合同价：壹佰万元整", 1000000),
        ("价款壹佰贰拾叁万肆仟伍佰陆拾柒元整", 1234567),
        ("总价贰拾万零伍佰元", 200500),
    ],
)
def test_parse_amount_text_supports_chinese_uppercase(text: str, expected: int) -> None:
    """合同条款大量使用大写金额；漏抽会让履约保证金/违约金规则整体失效。"""
    assert parse_amount_text(text) == expected


def test_parse_amount_text_prefers_arabic_when_both_present() -> None:
    assert parse_amount_text("合同总价 900000 元（大写：玖拾万元整）") == 900000


def test_parse_amount_text_ignores_non_amount_numbers() -> None:
    assert parse_amount_text("工期 120 天，质保期 1 年。") is None
    assert parse_amount_text("违约金按合同金额的 5% 计算。") is None


def test_split_clauses_groups_by_clause_header() -> None:
    parsed = _parsed(
        "某项目施工合同",
        "第一条 合同价格",
        "本合同总价为人民币 500 万元。",
        "第二条 违约责任",
        "乙方违约的，应支付合同总价 20% 的违约金。",
        "其他补充说明文字。",
    )
    clauses = split_clauses(parsed)
    assert [clause["title"] for clause in clauses][:2] == [
        "某项目施工合同",
        "第一条 合同价格",
    ]
    price = next(clause for clause in clauses if clause["category"] == "price")
    assert "500 万元" in price["text"]
    penalty = next(clause for clause in clauses if clause["category"] == "penalty")
    assert "违约金" in penalty["text"]


def test_back_to_back_rule_hits() -> None:
    clauses = _clauses(
        "第一条 付款方式",
        "发包人向业主收款后，再向乙方支付合同款项，以业主支付为前提。",
    )
    findings = apply_risk_rules(clauses)
    hits = [f for f in findings if f["rule_id"] == "back_to_back_payment"]
    assert hits and hits[0]["severity"] == "high"
    assert "背靠背" in hits[0]["message"]


def test_penalty_cap_missing_rule() -> None:
    # 缺失类规则要求正文达到最小长度（正文过短走 unknown 语义，见三态测试）
    filler = "双方经友好协商，就本项目达成如下条款，明确双方权利义务与履行安排，本合同经双方签署后生效，对双方均具有法律约束力，双方应共同遵守执行。" * 4
    clauses = _clauses(
        "第一条 违约责任",
        f"乙方违约的，应按合同总价的 30% 支付违约金。{filler}",
    )
    findings = apply_risk_rules(clauses)
    assert any(f["rule_id"] == "penalty_cap_missing" for f in findings)

    ok_clauses = _clauses(
        "第一条 违约责任",
        f"乙方违约的，应支付违约金，累计不超过合同总价的 10%。{filler}",
    )
    assert not any(f["rule_id"] == "penalty_cap_missing" for f in apply_risk_rules(ok_clauses))


def test_missing_section_rules() -> None:
    filler = "双方经友好协商，就本项目达成如下条款，明确双方权利义务与履行安排，本合同经双方签署后生效，对双方均具有法律约束力，双方应共同遵守执行。" * 4
    clauses = _clauses("第一条 合同价格", f"合同总价为人民币 100 万元。{filler}")
    findings = apply_risk_rules(clauses)
    rule_ids = {f["rule_id"] for f in findings}
    assert {"confidentiality_missing", "ip_missing", "dispute_missing"} <= rule_ids


def test_tri_party_price_deviation() -> None:
    clauses = _clauses(
        "第一条 合同价格",
        "本合同总价为人民币 620 万元。",
        "第二条 付款",
        "合同签订后支付 30%。",
    )
    payload = {"budget": {"amount": "5000000"}, "substantive_requirements": [], "timeline": []}
    findings = tri_party_consistency(clauses, payload)
    deviations = [f for f in findings if f["kind"] == "deviation"]
    assert deviations and deviations[0]["severity"] == "high"
    assert "第四十六条" in (deviations[0]["legal_basis"] or "")


def test_tri_party_missing_requirements() -> None:
    clauses = _clauses("第一条 合同价格", "本合同总价为人民币 500 万元。")
    payload = {
        "budget": {"amount": "5000000"},
        "substantive_requirements": [
            {"text": "投标人须提供原厂授权及售后服务承诺书"},
        ],
        "timeline": [],
    }
    findings = tri_party_consistency(clauses, payload)
    missing = [f for f in findings if f["kind"] == "missing"]
    assert missing and "原厂授权" in missing[0]["message"]


def test_extract_milestones_kinds_and_dates() -> None:
    from app.modules.contracts.service import extract_milestones

    clauses = _clauses(
        "第一条 付款方式\n甲方应于 2027年3月15日 前支付进度款 100 万元。",
        "第二条 质保\n质保期 24 个月，自验收合格起算。",
    )
    milestones = extract_milestones(clauses)
    kinds = {m["kind"] for m in milestones}
    assert {"payment", "warranty"} <= kinds
    payment = next(m for m in milestones if m["kind"] == "payment" and m["due_at"] is not None)
    assert payment["due_at"] is not None
    assert payment["due_at"].year == 2027 and payment["due_at"].month == 3


def test_tenant_custom_rule_hits_once() -> None:
    from types import SimpleNamespace

    from app.modules.contracts.service import apply_tenant_rules

    rules = [
        SimpleNamespace(
            name="禁止转包",
            pattern=r"转包|违法分包",
            severity="high",
            message_template="发现转包/违法分包措辞",
            enabled=True,
        ),
        SimpleNamespace(
            name="坏正则",
            pattern=r"([",
            severity="low",
            message_template="x",
            enabled=True,
        ),
        SimpleNamespace(
            name="已停用",
            pattern=r".*",
            severity="low",
            message_template="x",
            enabled=False,
        ),
    ]
    clauses = _clauses("第一条 分包", "乙方不得将工程转包或违法分包。")
    findings = apply_tenant_rules(clauses, rules)
    assert len(findings) == 1
    assert findings[0]["rule_id"] == "tenant:禁止转包"
    assert findings[0]["severity"] == "high"


def test_supplement_diff_flags_new_and_changed() -> None:
    from app.modules.contracts.service import diff_supplement

    base = _clauses("第一条 合同价格\n本合同总价为人民币 500 万元。")
    supplement = _clauses(
        "第一条 合同价格\n本合同总价调整为人民币 560 万元。",
        "第二条 保密义务\n双方对履约过程中知悉的商业秘密承担保密义务。",
    )
    findings = diff_supplement(base, supplement)
    rule_ids = {f["rule_id"] for f in findings}
    assert "supplement_changed_clause" in rule_ids
    assert "supplement_new_clause" in rule_ids
    changed = next(f for f in findings if f["rule_id"] == "supplement_changed_clause")
    assert changed["severity"] == "high"


def test_tenant_rule_inputs_are_bounded() -> None:
    from pydantic import ValidationError

    from app.modules.contracts.router import TenantRuleCreate

    with pytest.raises(ValidationError):
        TenantRuleCreate(name="x" * 51, pattern="a", message_template="m")
    with pytest.raises(ValidationError):
        TenantRuleCreate(name="ok", pattern="x" * 201, message_template="m")
    with pytest.raises(ValidationError):
        TenantRuleCreate(name="ok", pattern="a", message_template="m" * 2001)
    rule = TenantRuleCreate(name="ok", pattern="a", message_template="m")
    assert rule.severity == "medium"
