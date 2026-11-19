import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

SOURCE_PATH = Path(__file__).resolve().parents[2] / "data" / "policy_sources.json"


@dataclass(frozen=True)
class PolicySource:
    id: str
    title: str
    issuer: str
    doc_no: str | None
    date: str | None
    url: str | None
    note: str | None


@lru_cache(maxsize=1)
def load_policy_sources() -> tuple[str, tuple[PolicySource, ...]]:
    payload = json.loads(SOURCE_PATH.read_text(encoding="utf-8"))
    sources = tuple(
        PolicySource(
            id=str(item["id"]),
            title=str(item["title"]),
            issuer=str(item.get("issuer") or ""),
            doc_no=item.get("doc_no"),
            date=item.get("date"),
            url=item.get("url"),
            note=item.get("note"),
        )
        for item in payload.get("sources") or []
    )
    return str(payload.get("version") or ""), sources


def _normalize(value: str) -> str:
    return re.sub(r"\s+", "", value).lower()


def _doc_number_token(doc_no: str | None) -> str | None:
    if not doc_no:
        return None
    match = re.search(r"〔20\d{2}〕\d+号", doc_no)
    return match.group(0) if match else None


def verify_citation(text: str) -> dict[str, Any]:
    version, sources = load_policy_sources()
    normalized = _normalize(text)
    # 长标题优先：否则《招标投标法实施条例》会先命中《招标投标法》
    ordered = sorted(sources, key=lambda item: len(item.title), reverse=True)
    for source in ordered:
        tokens = [source.title, source.doc_no or ""]
        token = _doc_number_token(source.doc_no)
        if token:
            tokens.append(token)
        if any(t and _normalize(t) in normalized for t in tokens):
            return {
                "status": "verified",
                "source": {
                    "id": source.id,
                    "title": source.title,
                    "issuer": source.issuer,
                    "doc_no": source.doc_no,
                    "date": source.date,
                    "url": source.url,
                    "note": source.note,
                },
                "corpus_version": version,
            }
    identifiers = re.findall(
        r"[\u4e00-\u9fa5]{2,30}(?:法|条例|办法|规定|意见|通知)|[\u4e00-\u9fa5]{2,10}〔20\d{2}〕\d+号|GB\s*\d{4,5}",
        text,
    )
    return {
        "status": "out_of_corpus" if identifiers else "not_found",
        "source": None,
        "corpus_version": version,
        "identifiers": identifiers[:10],
    }
