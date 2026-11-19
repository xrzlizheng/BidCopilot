from app.modules.generation.factcheck import extract_claims, find_source, normalize_claim
from app.modules.generation.service import _constraint_corpus


def test_budget_wan_claim_matches_original_quote() -> None:
    payload = {
        "budget": {
            "amount": "4500000",
            "source_span": {"quote": "采购预算：人民币 450 万元。"},
        }
    }
    corpus = _constraint_corpus(payload)

    assert find_source("450万元", corpus) is not None
    assert find_source("4500000 元", corpus) is not None


def test_extract_claims() -> None:
    claims = extract_claims("报价 1,200,000 元，工期 12 个月，2026年11月10日，40分，PMP-001。")

    types = {claim["claim_type"] for claim in claims}
    assert {"amount", "duration", "date", "score", "standard"} <= types
    assert any(claim["claim"] == "1,200,000 元" for claim in claims)


def test_find_source_normalizes_spacing_and_commas() -> None:
    corpus = [
        {
            "source_type": "tender",
            "source_ref": "budget",
            "quote": "预算金额 1200000 元",
        }
    ]

    source = find_source("1,200,000 元", corpus)

    assert source is not None
    assert source["source_ref"] == "budget"


def test_unsourced_claim_returns_none() -> None:
    assert find_source("999999 元", [{"quote": "预算金额 1200000 元"}]) is None
    assert normalize_claim("1,200,000 元") == "1200000元"
