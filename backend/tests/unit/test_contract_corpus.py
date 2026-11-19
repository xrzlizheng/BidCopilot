"""合同规则语料回归门禁（三层回归结构吸收自 ContractGuard，MIT；语料自写）。

每份合成合同（txt + 期望 json 对）钉死至少 3 条规则的判定结果；含履约保证金
阈值反转对（15% 命中 / 9.9% 与恰好 10% 不命中），防止规则阈值或解析器改动
悄悄改变真实合同的判定结果。全部离线、无 DB、无 LLM。
"""

import json
from pathlib import Path

import pytest

from app.modules.contracts.service import apply_risk_rules, split_clauses
from app.modules.tender.parser import parse_document

CORPUS_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "contract_corpus"


def _corpus_cases() -> list[Path]:
    return sorted(CORPUS_DIR.glob("*.json"))


@pytest.mark.parametrize("spec_path", _corpus_cases(), ids=lambda p: p.stem)
def test_corpus_case(spec_path: Path) -> None:
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    text_path = spec_path.with_suffix(".txt")
    assert text_path.exists(), f"语料缺少对应 txt：{text_path.name}"

    content = text_path.read_bytes()
    parsed = parse_document(content, text_path.name)
    clauses = split_clauses(parsed)
    findings = apply_risk_rules(clauses)

    hit_rules = {f["rule_id"] for f in findings if f["kind"] == "risk"}
    unknown_rules = {f["rule_id"] for f in findings if f["kind"] == "unknown"}

    for rule_id in spec.get("expect_violations", []):
        assert rule_id in hit_rules, (
            f"{spec_path.stem}: 期望命中 {rule_id}，实际命中 {sorted(hit_rules)}"
            f"（unknown={sorted(unknown_rules)}）"
        )
    for rule_id in spec.get("expect_absent", []):
        assert rule_id not in hit_rules, (
            f"{spec_path.stem}: 期望不命中 {rule_id}，实际命中 {sorted(hit_rules)}"
        )


def test_corpus_covers_all_rules_and_has_threshold_pair() -> None:
    """门禁自身的完整性：每条规则至少被一份语料显式钉住；阈值反转对成对存在。"""
    specs = [json.loads(p.read_text(encoding="utf-8")) for p in _corpus_cases()]
    covered = set()
    for spec in specs:
        covered.update(spec.get("expect_violations", []))
        covered.update(spec.get("expect_absent", []))
    from app.modules.contracts.service import RULES

    rule_ids = {rule["rule_id"] for rule in RULES}
    assert rule_ids <= covered, f"规则未被语料钉住：{sorted(rule_ids - covered)}"

    stems = {p.stem for p in _corpus_cases()}
    assert {"deposit_over_15", "deposit_edge_9_9", "deposit_edge_10"} <= stems, (
        "履约保证金阈值反转对缺失"
    )
