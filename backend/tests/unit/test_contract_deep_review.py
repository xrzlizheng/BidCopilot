"""合同审查升级件单测：审查点模板匹配、深审分批/引文回查/引用校验、
占位符规则、三态 unknown 语义、修订对照 diff（无 DB）。"""

from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from app.modules.contracts.models import ContractFinding, LawArticle
from app.modules.contracts.service import (
    DEEP_REVIEW_KINDS,
    MIN_TEXT_FOR_MISSING_CHECK,
    _batch_clauses,
    _cn_article_no_to_int,
    _deep_review_system,
    _rule_placeholder_blank_fields,
    _verify_citations,
    _verify_quote,
    apply_risk_rules,
    classify_clause,
    diff_re_review,
    extract_milestones,
    match_review_templates,
    recall_law_articles,
)


def _template(
    contract_type: str,
    keywords: list[str],
    review_points: list[str] | None = None,
    *,
    is_system: bool = True,
    template_id=None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=template_id or uuid4(),
        is_system=is_system,
        contract_type=contract_type,
        name=f"{contract_type}模板",
        contract_type_keywords=keywords,
        review_points=review_points or ["审查点A", "审查点B"],
        core_purposes=["目的1"],
        prompt_rules=["纪律1"],
        enabled=True,
    )


def _clause(text: str, seq: int = 1, category: str | None = None) -> dict:
    return {
        "seq": seq,
        "title": text[:20],
        "text": text,
        "category": category or classify_clause(text),
        "page": 1,
    }


# —— 引文回查 _verify_quote ——


def test_verify_quote_exact_and_normalized_and_miss() -> None:
    source = "甲方应当在 30 日内 甲方应支付 款项"
    assert _verify_quote("甲方应支付", source) is not None
    # 去空白归一化命中并把偏移映射回原文
    offsets = _verify_quote("甲方应当在30日内", source)
    assert offsets is not None
    assert source[offsets[0] : offsets[1]] == "甲方应当在 30 日内"
    assert _verify_quote("完全无关", source) is None
    assert _verify_quote("", source) is None


def test_verify_quote_empty_source_and_whitespace_only() -> None:
    assert _verify_quote("x", "") is None
    assert _verify_quote("   ", "abc") is None


# —— 条号折算与引用校验 ——


def test_cn_article_no_to_int_variants() -> None:
    assert _cn_article_no_to_int("586") == 586
    assert _cn_article_no_to_int("５８６") == 586  # 全角
    assert _cn_article_no_to_int("五百八十六") == 586
    assert _cn_article_no_to_int("第十条") == 10
    assert _cn_article_no_to_int("第二十六条") == 26
    assert _cn_article_no_to_int("两百日") is None  # 非"条"号形态按 None 处理不再断言值
    assert _cn_article_no_to_int("廿一") is None


def test_verify_citations_three_states() -> None:
    kb = {"中华人民共和国民法典": {585, 586}, "中华人民共和国招标投标法实施条例": {58}}
    assert _verify_citations("《中华人民共和国民法典》第五百八十五条", kb) is True
    mixed = "《中华人民共和国民法典》第五百八十五条、《中华人民共和国招标投标法实施条例》第九十九条"
    assert _verify_citations(mixed, kb) is False
    assert _verify_citations("参照最高人民法院裁判倾向", kb) is None
    assert _verify_citations(None, kb) is None


def test_recall_law_articles_scores_and_limits() -> None:
    laws = [
        LawArticle(
            law_name="民法典", article_no="第五百八十五条", article_no_arabic=585,
            article_text="违约金", keywords=["违约金", "penalty"], content_hash="h1",
        ),
        LawArticle(
            law_name="民法典", article_no="第七百零五条", article_no_arabic=705,
            article_text="租赁", keywords=["租赁"], content_hash="h2",
        ),
    ]
    batch = [SimpleNamespace(text="本条约定违约金按日千分之三计。")]
    recalled = recall_law_articles(laws, batch)
    assert [a.article_no_arabic for a in recalled] == [585]
    empty = recall_law_articles(laws, [SimpleNamespace(text="与法条无关的文本")])
    assert empty == []


# —— 深审分批与 prompt ——


def _deep_clause(seq: int, size: int) -> SimpleNamespace:
    return SimpleNamespace(seq=seq, title=f"第{seq}条", text="款" * size)


def test_batch_clauses_groups_by_budget_and_keeps_order() -> None:
    clauses = [_deep_clause(i, 50) for i in range(1, 6)]
    batches = _batch_clauses(clauses, 120)
    assert len(batches) >= 2
    flattened = [clause.seq for batch in batches for clause in batch]
    assert flattened == [1, 2, 3, 4, 5]  # 顺序保持


def test_batch_clauses_oversized_clause_gets_own_batch() -> None:
    # 单条成本按截断上限（DEEP_REVIEW_CLAUSE_TEXT_CAP=2000）计；预算小于该上限时
    # 超限条款独立成批，后续条款进下一批，不拆条款也不丢弃。
    huge = SimpleNamespace(seq=1, title="巨条", text="款" * 5000)
    small = SimpleNamespace(seq=2, title="小条", text="款" * 10)
    batches = _batch_clauses([huge, small], 1200)
    assert len(batches) == 2
    assert [clause.seq for clause in batches[0]] == [1]
    assert [clause.seq for clause in batches[1]] == [2]


def test_batch_clauses_empty_input() -> None:
    assert _batch_clauses([], 6000) == []


def test_deep_review_system_injects_template_assets_and_discipline() -> None:
    prompt = _deep_review_system(
        ["核查付款节点", "核查履约保证金比例"], ["付款与违约对等"], ["不得建议违法条款"]
    )
    assert "核查付款节点" in prompt
    assert "不得建议违法条款" in prompt
    assert "clause_seq" in prompt
    assert "逐字" in prompt  # evidence 回查契约
    assert '"unknown"' in prompt


def test_deep_review_system_injects_market_benchmarks() -> None:
    """backlog B6：市场常见基准参照（大陆法数字基准）必须注入且标注非强制。"""
    prompt = _deep_review_system([], [], [])
    assert "市场常见基准参照" in prompt
    assert "履约保证金" in prompt
    assert "非强制标准" in prompt


def test_placeholder_rule_matches_lowercase_xx() -> None:
    """backlog B3：小写 xx公司 同样是未实名占位。"""
    hits = _rule_placeholder_blank_fields([_clause("甲方为xx公司，乙方为乙公司。")], "甲方为xx公司。")
    assert len(hits) == 1


def test_diff_re_review_summary_carries_status() -> None:
    """backlog B2：diff 摘要携带 status，前端可标注已驳回噪音。"""
    dismissed = _finding("RULE-A", "保证金比例超限")
    dismissed.status = "dismissed"
    diff = diff_re_review([dismissed], [])
    assert diff["resolved"][0]["status"] == "dismissed"


def test_extract_milestones_relative_terms() -> None:
    """backlog B5：相对期限落 kind=relative，due_at 为空（锚点日期未知）。"""
    clauses = [
        _clause("验收合格后 30 日内支付合同总价款的 50%。", seq=1),
        _clause("自合同签订后 15 个工作日内提交实施方案。", seq=2),
    ]
    milestones = extract_milestones(clauses)
    relative = [m for m in milestones if m["kind"] == "relative"]
    assert len(relative) == 2
    assert all(m["due_at"] is None for m in relative)
    assert any("验收合格后 30 日" in m["title"] for m in relative)
    assert any("合同签订后 15 个工作日" in m["title"] for m in relative)
    # 相对期限不进到期提醒口径（调度器只扫 due_at 非空）
    absolute = extract_milestones([_clause("甲方应于 2027年3月15日 前支付进度款。")])
    assert any(m["due_at"] is not None for m in absolute)


# —— 模板匹配 ——


def test_match_review_templates_strong_hit_and_mixed_union() -> None:
    labor = _template("labor", ["劳动", "雇佣", "员工", "用工"])
    lease = _template("lease", ["租赁", "承租", "出租"])
    general = _template("general", [])
    clauses = [_clause("甲方雇佣乙方为员工，负责用工管理。")]

    matched = match_review_templates([labor, lease, general], clauses)
    assert matched["selected"] == ["labor"]
    assert matched["matched"][0]["basis"] == "keyword"
    assert matched["matched"][0]["score"] >= 2

    mixed = match_review_templates(
        [labor, lease, general],
        [_clause("甲方雇佣乙方员工承担租赁物维护，承租人义务由出租人监督。")],
    )
    assert len(mixed["selected"]) == 2
    assert mixed["matched"][1]["basis"] == "keyword_mixed"


def test_match_review_templates_falls_back_to_general() -> None:
    labor = _template("labor", ["劳动", "雇佣"])
    general = _template("general", [])
    matched = match_review_templates([labor, general], [_clause("纯商务条款，无行业词。")])
    assert matched["selected"] == ["general"]
    assert matched["matched"][0]["basis"] == "fallback_general"


def test_match_review_templates_manual_ids_win_and_extras_lead() -> None:
    labor = _template("labor", ["劳动"])
    lease = _template("lease", ["租赁"])
    matched = match_review_templates(
        [labor, lease],
        [_clause("劳动用工条款。")],
        template_ids=[str(lease.id)],
        extra_review_points=["关注垫资条款", "审查点A"],
    )
    assert matched["selected"] == ["lease"]
    assert matched["matched"][0]["basis"] == "manual"
    # 用户补充关注点在最前，且与模板审查点去重
    assert matched["applied_review_points"][0] == "关注垫资条款"
    assert matched["applied_review_points"].count("审查点A") == 1


def test_match_review_templates_core_purpose_override() -> None:
    labor = _template("labor", ["劳动"], ["审查点A"])
    matched = match_review_templates(
        [labor], [_clause("劳动条款。")], core_purpose="聚焦试用期合规"
    )
    assert matched["core_purposes"] == ["聚焦试用期合规"]


# —— 占位符规则 ——


def test_placeholder_rule_flags_all_classes() -> None:
    hits = _rule_placeholder_blank_fields(
        [
            _clause("合同金额为____元整。"),
            _clause("交付地点为【 】。"),
            _clause("违约责任（待填）后补。"),
            _clause("付款时间另行确定。"),
            _clause("甲方为某某公司。"),
            _clause("验收标准详见附件。"),
        ],
        "".join("合同金额为____元整。交付地点为【 】。违约责任（待填）后补。付款时间另行确定。甲方为某某公司。验收标准详见附件。"),
    )
    assert len(hits) == 6


def test_placeholder_rule_attaches_only_when_no_exhibit_listed() -> None:
    clause = _clause("验收标准详见附件。")
    without = _rule_placeholder_blank_fields([clause], "验收标准详见附件。")
    assert len(without) == 1
    with_list = _rule_placeholder_blank_fields([clause], "验收标准详见附件：附件一 规范。")
    assert with_list == []


# —— 三态 unknown 语义 ——


def test_missing_check_rules_skipped_with_unknown_when_text_insufficient() -> None:
    tiny = [_clause("第一条 短文本。")]
    findings = apply_risk_rules(tiny)
    unknown = [f for f in findings if f["kind"] == "unknown"]
    assert len(unknown) == 1
    assert unknown[0]["rule_id"] == "parsing_insufficient"
    assert unknown[0]["severity"] == "low"
    # 缺失类规则不应在正文过短时误报
    assert all(f["rule_id"] != "payment_terms_missing" for f in findings)
    assert len("".join(c["text"] for c in tiny)) < MIN_TEXT_FOR_MISSING_CHECK


def test_rules_findings_carry_confidence_and_detection() -> None:
    findings = apply_risk_rules(
        [
            _clause("第一条 合同价格：本合同总价为人民币 500 万元。"),
            _clause("第二条 履约保证金：乙方应缴纳履约保证金 100 万元。"),
            _clause("第三条 付款：验收合格后支付合同总价款。"),
            _clause("第四条 违约责任：任何一方违约的，按合同法承担违约责任。"),
            _clause("第五条 争议解决：提交合同签订地人民法院诉讼。"),
        ]
    )
    assert findings, "正常长度文本应命中至少一条规则"
    for finding in findings:
        assert finding["confidence"] == Decimal("1.00")
        assert finding["detection_method"] == "rule"


# —— 修订对照 diff ——


def _finding(rule_id: str | None, message: str, source: str = "rule") -> ContractFinding:
    return ContractFinding(
        review_id=uuid4(),
        kind="risk",
        rule_id=rule_id,
        severity="medium",
        message=message,
        evidence_span={},
        source=source,
    )


def test_diff_re_review_added_resolved_carried() -> None:
    old = [_finding("RULE-A", "保证金比例超限"), _finding("RULE-B", "缺少违约金上限")]
    new = [_finding("RULE-B", "缺少违约金上限"), _finding("RULE-C", "背靠背支付条款")]
    diff = diff_re_review(old, new)
    assert [item["rule_id"] for item in diff["added"]] == ["RULE-C"]
    assert [item["rule_id"] for item in diff["resolved"]] == ["RULE-A"]
    assert [item["rule_id"] for item in diff["carried"]] == ["RULE-B"]


def test_deep_review_kinds_allow_unknown() -> None:
    assert "unknown" in DEEP_REVIEW_KINDS
