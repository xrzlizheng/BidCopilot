import json
from dataclasses import dataclass
from pathlib import Path

RULE_PATH = Path(__file__).resolve().parents[2] / "data" / "rules" / "compliance_rules.json"


@dataclass(frozen=True)
class Rule:
    rule_id: str
    name: str
    category: str
    type: str
    severity: str
    expect: str
    basis: str
    auto_fixable: bool
    needs_review: bool
    threshold: float | None = None


def load_rule_set() -> tuple[str, list[Rule]]:
    payload = json.loads(RULE_PATH.read_text(encoding="utf-8"))
    version = str(payload["version"])
    rules = [Rule(**item) for item in payload["rules"]]
    ids = [rule.rule_id for rule in rules]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate compliance rule ids")
    for rule in rules:
        if rule.type not in {"HARD", "SOFT"}:
            raise ValueError(f"invalid rule type: {rule.rule_id}")
    return version, rules
