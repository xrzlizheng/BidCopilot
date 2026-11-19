import re
from typing import Any

from app.modules.generation.policy_sources import verify_citation

CLAIM_PATTERNS = (
    ("amount", re.compile(r"\d[\d,]*(?:\.\d+)?\s*(?:万元|元)")),
    ("date", re.compile(r"20\d{2}\s*[-年/]\s*\d{1,2}\s*[-月/]\s*\d{1,2}\s*日?")),
    ("percent", re.compile(r"\d+(?:\.\d+)?\s*%")),
    ("duration", re.compile(r"\d+\s*(?:个月|年|天|日)")),
    ("score", re.compile(r"\d+(?:\.\d+)?\s*分")),
    ("standard", re.compile(r"[A-Z]{2,}(?:/[A-Z])?[- ]?\d{2,}")),
)

POLICY_CITATION_PATTERN = re.compile(
    r"《[^《》]{4,60}(?:法|条例|办法|规定|意见|通知|细则)》"
    r"|(?:发改法规|国办发|财库|工信部(?:联)?|国能发|教职成|税总|银发|证监发)?〔20\d{2}〕\d+号"
)


def normalize_claim(value: str) -> str:
    return re.sub(r"[\s,，。；;：:（）()]", "", value).lower()


def extract_claims(text: str) -> list[dict[str, str]]:
    claims: list[dict[str, str]] = []
    seen: set[str] = set()
    for claim_type, pattern in CLAIM_PATTERNS:
        for match in pattern.finditer(text):
            claim = match.group(0).strip()
            key = normalize_claim(claim)
            if not key or key in seen:
                continue
            seen.add(key)
            claims.append({"claim": claim, "claim_type": claim_type})
    return claims[:200]


def extract_policy_citations(text: str) -> list[dict[str, str]]:
    """政策引用单独抽出来核验：语料内=已核实，未收录=待人工复核，不冒充已验证。"""
    citations: list[dict[str, str]] = []
    seen: set[str] = set()
    for match in POLICY_CITATION_PATTERN.finditer(text or ""):
        citation = match.group(0).strip()
        key = normalize_claim(citation)
        if not key or key in seen:
            continue
        seen.add(key)
        citations.append({"claim": citation, "claim_type": "policy_citation"})
    return citations[:50]


def find_source(claim: str, corpus: list[dict[str, Any]]) -> dict[str, Any] | None:
    normalized = normalize_claim(claim)
    for source in corpus:
        quote = source.get("quote") or ""
        if normalized and normalized in normalize_claim(quote):
            return source
    return None


def verify_policy_claim(claim: str) -> dict[str, Any]:
    """返回 {status, source}：verified / out_of_corpus / not_found。"""
    return verify_citation(claim)
