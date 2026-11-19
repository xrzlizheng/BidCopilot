import pytest

from app.modules.generation.factcheck import (
    extract_policy_citations,
    verify_policy_claim,
)


def test_policy_citations_are_extracted_and_verified_against_corpus() -> None:
    text = (
        "本方案依据《中华人民共和国招标投标法实施条例》第二十六条执行，"
        "并对照发改法规〔2026〕195号推进合规自查，"
        "另引用《某虚构管理办法》第十条。"
    )
    citations = extract_policy_citations(text)
    claims = {item["claim"] for item in citations}
    assert "《中华人民共和国招标投标法实施条例》" in claims
    assert "发改法规〔2026〕195号" in claims
    assert all(item["claim_type"] == "policy_citation" for item in citations)

    verified = verify_policy_claim("《中华人民共和国招标投标法实施条例》第二十六条")
    assert verified["status"] == "verified"
    assert verified["source"]["doc_no"] == "国务院令第709号"

    out_of_corpus = verify_policy_claim("《某虚构管理办法》第十条")
    assert out_of_corpus["status"] == "out_of_corpus"
    assert out_of_corpus["source"] is None

    not_found = verify_policy_claim("这是一段普通描述")
    assert not_found["status"] == "not_found"


@pytest.mark.parametrize("text", ["", "没有政策引用的段落", "金额 120 万元，2026年11月10日"])
def test_policy_citations_ignore_plain_text(text: str) -> None:
    assert extract_policy_citations(text) == []
