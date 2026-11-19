from types import SimpleNamespace

from app.modules.compliance.policies import apply_policies
from app.modules.compliance.registry import load_rule_set


def test_apply_policies_disables_and_overrides_severity() -> None:
    _, rules = load_rule_set()
    disabled_id = rules[0].rule_id
    override_id = rules[1].rule_id
    threshold_id = rules[2].rule_id
    policies = [
        SimpleNamespace(rule_id=disabled_id, enabled=False, severity_override=None, threshold_override=None),
        SimpleNamespace(rule_id=override_id, enabled=True, severity_override="low", threshold_override=None),
        SimpleNamespace(rule_id=threshold_id, enabled=True, severity_override=None, threshold_override=0.3),
    ]

    effective = apply_policies(rules, policies)

    ids = {rule.rule_id for rule in effective}
    assert disabled_id not in ids
    assert next(rule for rule in effective if rule.rule_id == override_id).severity == "low"
    assert next(rule for rule in effective if rule.rule_id == threshold_id).threshold == 0.3
    assert len(effective) == len(rules) - 1
