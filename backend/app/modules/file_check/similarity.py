import re
import unicodedata
from difflib import SequenceMatcher

SHINGLE_SIZE = 4


def normalize(text: str) -> str:
    value = unicodedata.normalize("NFKC", text).lower()
    return re.sub(r"[\s\W_]+", "", value)


def shingles(text: str) -> set[str]:
    value = normalize(text)
    if len(value) <= SHINGLE_SIZE:
        return {value} if value else set()
    return {value[index : index + SHINGLE_SIZE] for index in range(len(value) - SHINGLE_SIZE + 1)}


def common_excerpt(left: str, right: str, *, limit: int = 200) -> str | None:
    a = normalize(left)[:20000]
    b = normalize(right)[:20000]
    if not a or not b:
        return None
    match = SequenceMatcher(None, a, b, autojunk=False).find_longest_match(0, len(a), 0, len(b))
    if match.size < 40:
        return None
    return a[match.a : match.a + min(match.size, limit)]


def compare(left: str, right: str) -> dict:
    left_set = shingles(left)
    right_set = shingles(right)
    union = left_set | right_set
    score = len(left_set & right_set) / len(union) if union else 0.0
    if score >= 0.75:
        level = "high"
    elif score >= 0.5:
        level = "medium"
    else:
        level = "low"
    return {
        "score": round(score, 4),
        "level": level,
        "common_excerpt": common_excerpt(left, right) if level != "low" else None,
    }
