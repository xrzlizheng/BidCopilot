from types import SimpleNamespace

from app.modules.compliance.consistency import check_open_responses, check_unlinked_responses
from app.modules.compliance.engine import HANDLERS, _finding
from app.modules.compliance.registry import Rule, load_rule_set

RULE = Rule(
    rule_id="RESP-OPEN-001",
    name="存在未处理的响应条目",
    category="responsiveness",
    type="HARD",
    severity="high",
    expect="全部已响应",
    basis="招标投标法第 27 条",
    auto_fixable=False,
    needs_review=True,
)
LINK_RULE = Rule(
    rule_id="RESP-LINK-001",
    name="已响应条目未指向具体章节",
    category="responsiveness",
    type="SOFT",
    severity="medium",
    expect="已响应条目可定位到章节",
    basis="清标按条款核对响应位置",
    auto_fixable=False,
    needs_review=True,
)


def _row(status: str, text: str = "投标人须具备营业执照") -> SimpleNamespace:
    return SimpleNamespace(response_status=status, requirement_text=text)


def test_pending_and_deviated_items_are_reported() -> None:
    context = SimpleNamespace(
        responses=[_row("pending"), _row("deviated"), _row("responded"), _row("not_responded")]
    )
    findings = check_open_responses(RULE, context, _finding)
    assert len(findings) == 1
    assert findings[0]["rule_id"] == "RESP-OPEN-001"
    actual = findings[0]["actual"]
    assert "待确认1" in actual and "偏离1" in actual and "不响应1" in actual


def test_all_responded_passes() -> None:
    context = SimpleNamespace(responses=[_row("responded"), _row("responded")])
    assert check_open_responses(RULE, context, _finding) == []


def test_no_response_rows_means_rule_not_applicable() -> None:
    assert check_open_responses(RULE, SimpleNamespace(responses=[]), _finding) == []


def test_responded_without_section_is_reported() -> None:
    context = SimpleNamespace(
        responses=[
            SimpleNamespace(response_status="responded", requirement_text="技术方案", section_ref=None),
            SimpleNamespace(response_status="responded", requirement_text="项目业绩", section_ref="  "),
            SimpleNamespace(response_status="pending", requirement_text="服务承诺", section_ref=None),
        ]
    )
    findings = check_unlinked_responses(LINK_RULE, context, _finding)
    assert len(findings) == 1
    assert findings[0]["rule_id"] == "RESP-LINK-001"
    assert findings[0]["finding_type"] == "SOFT"
    assert "2 条" in findings[0]["actual"]
    # 待确认条目由 RESP-OPEN-001 负责，这里不重复报
    assert "服务承诺" not in findings[0]["actual"]


def test_linked_and_unresponsive_items_pass_the_link_rule() -> None:
    context = SimpleNamespace(
        responses=[
            SimpleNamespace(response_status="responded", requirement_text="技术方案", section_ref="技术方案响应"),
            SimpleNamespace(response_status="not_responded", requirement_text="垫资要求", section_ref=None),
        ]
    )
    assert check_unlinked_responses(LINK_RULE, context, _finding) == []


def test_every_library_rule_is_dispatchable() -> None:
    _version, rules = load_rule_set()
    rule_ids = {rule.rule_id for rule in rules}
    assert "RESP-LINK-001" in rule_ids, "新规则必须进入规则库而不是只加 handler"
    missing = [rule_id for rule_id in rule_ids if rule_id not in HANDLERS]
    assert not missing, f"rules without handlers: {missing}"
    unknown = [rule_id for rule_id in HANDLERS if rule_id not in rule_ids]
    assert not unknown, f"handlers without rule definition: {unknown}"
