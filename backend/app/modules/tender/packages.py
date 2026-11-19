import re
from collections.abc import Callable
from typing import Any

PACKAGE_ITEM = re.compile(
    r"^(?:"
    r"标包\s*([0-9]+|[一二三四五六七八九十]+)"
    r"|第?([0-9]+|[一二三四五六七八九十]+)\s*标包"
    r"|包\s*([0-9]+)"
    r"|([A-Za-z])\s*包"
    r")\s*[：:]?\s*(.*)$"
)
PACKAGE_DECL = re.compile(
    r"(?:本项目|招标项目|采购项目)?[^。；\n]{0,12}?(?:划分为|分为)\s*"
    r"([0-9]+|[一二三四五六七八九十]+)\s*个?\s*标包"
)
CN_COUNTS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def package_count(token: str) -> int | None:
    if token.isdigit():
        value = int(token)
    elif token in CN_COUNTS:
        value = CN_COUNTS[token]
    else:
        return None
    return value if 1 < value <= 50 else None


def extract_packages(blocks: list[Any], source_span: Callable[[Any], dict[str, Any]]) -> dict[str, Any]:
    """识别多标包招标：声明的包数，以及“标包一：xxx”这类具名包件。"""
    declared: int | None = None
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for block in blocks:
        text = block.text.strip()
        if declared is None:
            match = PACKAGE_DECL.search(text)
            if match:
                declared = package_count(match.group(1))
        match = PACKAGE_ITEM.match(text)
        if not match:
            continue
        code = (
            match.group(1) or match.group(2) or match.group(3) or match.group(4) or ""
        ).strip()
        if not code or code in seen:
            continue
        seen.add(code)
        items.append(
            {
                "code": code,
                "name": (match.group(5) or "").strip(" ：:，,。")[:200],
                "source_span": source_span(block),
            }
        )
    return {"declared_count": declared, "items": items[:50]}
