
from app.modules.compliance.engine import HANDLERS, _finding
from app.modules.compliance.registry import Rule, load_rule_set
from app.modules.compliance.workload import (
    check_workload_cost,
    estimate_workload_cost,
)


def _rule(rule_id: str) -> Rule:
    _, rules = load_rule_set()
    return next(rule for rule in rules if rule.rule_id == rule_id)


class _Context:
    def __init__(self, text: str, cost_model: dict) -> None:
        self.content_text = text
        self.cost_model = cost_model


def test_workload_estimation_only_accepts_explicit_numbers() -> None:
    assert estimate_workload_cost("本项目投入 120 人天完成建设") == (120.0, "直接写明人天")
    days = estimate_workload_cost("投入 5 名技术人员，工期 60 天")
    assert days is not None and days[0] == 300.0
    assert estimate_workload_cost("项目团队将按计划推进") is None


def test_workload_cost_rule_flags_estimate_above_quote() -> None:
    context = _Context(
        "投标报价：100 万元。本项目投入 200 人天完成建设。",
        {"person_day_rate": 8000},
    )
    findings = check_workload_cost(_rule("WORKLOAD-COST-001"), context, _finding)
    assert len(findings) == 1
    assert "高于投标报价" in findings[0]["message"]

    calibrated = _Context(
        "投标报价：100 万元。本项目投入 200 人天完成建设。",
        {},
    )
    assert check_workload_cost(_rule("WORKLOAD-COST-001"), calibrated, _finding) == []


def test_workload_cost_rule_ignores_missing_quote_or_rate() -> None:
    no_quote = _Context("本项目投入 200 人天完成建设。", {"person_day_rate": 8000})
    assert check_workload_cost(_rule("WORKLOAD-COST-001"), no_quote, _finding) == []

    cheap = _Context(
        "投标报价：100 万元。本项目投入 10 人天完成建设。",
        {"person_day_rate": 8000},
    )
    assert check_workload_cost(_rule("WORKLOAD-COST-001"), cheap, _finding) == []


def test_workload_rule_has_handler() -> None:
    assert "WORKLOAD-COST-001" in HANDLERS
