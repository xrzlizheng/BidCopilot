"""Evaluate the tender parser against a labelled corpus.

Usage:
    cd backend
    uv run python ../scripts/eval_parser.py --corpus ../gold-corpus

Corpus layout:
    gold-corpus/labels.json
    gold-corpus/<docx|pdf|txt|md>

labels.json maps filename to expected labels:
    {
      "tender-a.docx": {
        "scoring_items": ["技术方案", "投标报价"],
        "qualifications": ["涉密信息系统集成乙级"],
        "reject_clauses": ["未提供有效营业执照"],
        "budget_amount": "1200000",
        "dark_bid": true
      }
    }
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from tests.gold_corpus import GoldCase, evaluate_gold_corpus  # noqa: E402


def load_cases(corpus: Path) -> list[GoldCase]:
    manifest = corpus / "labels.json"
    if not manifest.is_file():
        raise SystemExit(f"missing manifest: {manifest}")
    labels = json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(labels, dict) or not labels:
        raise SystemExit("labels.json must be a non-empty object keyed by filename")
    cases: list[GoldCase] = []
    for filename, label in labels.items():
        path = corpus / filename
        if not path.is_file():
            raise SystemExit(f"missing document: {path}")
        budget = label.get("budget_amount")
        cases.append(
            GoldCase(
                name=filename,
                filename=filename,
                content=path.read_bytes(),
                scoring_items=tuple(label.get("scoring_items", [])),
                qualifications=tuple(label.get("qualifications", [])),
                reject_clauses=tuple(label.get("reject_clauses", [])),
                budget_amount=str(budget) if budget is not None else None,
                dark_bid=bool(label.get("dark_bid", False)),
            )
        )
    return cases


def _micro(results: list, dimension: str) -> tuple[int, int, int]:
    matched = sum(getattr(result, dimension).matched for result in results)
    covered = sum(getattr(result, dimension).covered for result in results)
    predicted = sum(getattr(result, dimension).predicted for result in results)
    expected = sum(getattr(result, dimension).expected for result in results)
    return matched, covered, predicted, expected


def main() -> int:
    parser = argparse.ArgumentParser(description="BidCopilot parser gold corpus evaluation")
    parser.add_argument("--corpus", required=True, help="directory containing labels.json and documents")
    parser.add_argument("--min-f1", type=float, default=0.8, help="fail below this micro F1")
    args = parser.parse_args()

    try:
        results = evaluate_gold_corpus(load_cases(Path(args.corpus)))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        print(f"invalid corpus: {exc}", file=sys.stderr)
        return 2

    failed = False
    for result in results:
        print(
            f"{result.case}: "
            f"scoring P={result.scoring.precision:.3f} R={result.scoring.recall:.3f} F1={result.scoring.f1:.3f}; "
            f"qualification P={result.qualification.precision:.3f} R={result.qualification.recall:.3f} F1={result.qualification.f1:.3f}; "
            f"reject F1={result.reject.f1:.3f}; "
            f"budget={'ok' if result.budget_ok else 'FAIL'}; "
            f"dark_bid={'ok' if result.dark_bid_ok else 'FAIL'}"
        )
        failed = failed or not result.budget_ok or not result.dark_bid_ok

    for dimension in ("scoring", "qualification", "reject"):
        matched, covered, predicted, expected = _micro(results, dimension)
        precision = covered / predicted if predicted else 1.0
        recall = matched / expected if expected else 1.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        print(
            f"{dimension} micro: matched={matched} covered={covered} "
            f"predicted={predicted} expected={expected} "
            f"P={precision:.3f} R={recall:.3f} F1={f1:.3f}"
        )
        failed = failed or f1 < args.min_f1

    print(f"cases={len(results)} min_f1={args.min_f1:.2f} result={'FAIL' if failed else 'OK'}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
