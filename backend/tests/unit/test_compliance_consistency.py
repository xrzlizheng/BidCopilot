from types import SimpleNamespace

from app.modules.compliance.consistency import check_consistency_numbers
from app.modules.compliance.engine import _finding
from app.modules.compliance.registry import Rule

RULE = Rule(
    rule_id="CONSIST-NUM-001",
    name="跨章节数值不一致",
    category="consistency",
    type="SOFT",
    severity="medium",
    expect="同一指标在全文保持一致",
    basis="全文一致性自查",
    auto_fixable=False,
    needs_review=True,
)


def _run(text: str) -> list[dict]:
    return check_consistency_numbers(RULE, SimpleNamespace(content_text=text), _finding)


def test_consistent_numbers_report_nothing() -> None:
    text = "第一节 工期为 90 天。第二节 工期 90 日。第三节 工期 3 个月。"
    assert _run(text) == []


def test_conflicting_duration_is_reported() -> None:
    text = "第一节 工期为 90 天。第二节 工期为 120 天。"
    findings = _run(text)
    assert len(findings) == 1
    assert findings[0]["rule_id"] == "CONSIST-NUM-001"
    assert "工期" in findings[0]["message"]
    assert "90" in findings[0]["actual"] and "120" in findings[0]["actual"]


def test_response_time_units_are_normalized() -> None:
    same = "故障响应时间不超过 30 分钟。另一处写明响应时间 0.5 小时。"
    assert _run(same) == []
    different = "故障响应时间不超过 30 分钟。另一处写明响应时间 2 小时。"
    assert len(_run(different)) == 1


def test_availability_and_duration_are_independent_metrics() -> None:
    text = "全年可用性不低于 99.95%。工期为 90 天。质保期 2 年。"
    assert _run(text) == []


def test_unit_mismatch_for_concept_is_ignored() -> None:
    # 可用性用百分比表达，混入“天”不应被当成同一指标的不同取值
    text = "全年可用性不低于 99.95%。可用性 30 天。"
    assert _run(text) == []
