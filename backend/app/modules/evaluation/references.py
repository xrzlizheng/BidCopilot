"""标书内部引用的可解析性检查。

评标专家有一条基本警惕：**"详见附件 5"不等于"附件 5 已提供"**。模型看到"详见/参见"
很容易默认证据存在，从而给高分。这里把引用抽出来，能解析的标 resolved，解析不了的
显式写进证据，告诉模型"这条引用无法核实，不得当作已提供证据"。

只做确定性、不越权的检查：
- 章节引用（第 N 章/节）：对照章节顺序号，不存在即 dangling；
- 附件/附录引用：系统内没有标书附件的建模，一律 unverifiable；
- 《文件名》引用：要跨知识库鉴权才能确认，不查库，一律 unverifiable。

宁可标"无法核实"，也不假装核实过了 —— 评标结论一旦建立在悬空引用上，就是废标级风险。
"""

from __future__ import annotations

import re
from typing import Any

CN_DIGITS = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}

SECTION_REF_PATTERN = re.compile(r"第\s*([0-9一二三四五六七八九十百]+)\s*[章节]")
ATTACHMENT_REF_PATTERN = re.compile(r"(附件|附录|附表|附图)\s*([0-9一二三四五六七八九十]*)?")
DOCUMENT_REF_PATTERN = re.compile(r"《([^》\n]{2,60})》")

# "按招标文件第三章要求响应"引用的是招标文件，不是投标文件内部引用。
# 不排除会把大量合规表述误判成悬空引用。
EXTERNAL_HINTS = ("招标", "采购文件", "磋商", "询价", "谈判文件", "资格预审文件")

STATUS_RESOLVED = "resolved"
STATUS_DANGLING = "dangling"
STATUS_UNVERIFIABLE = "unverifiable"


def _is_external(text: str, start: int) -> bool:
    prefix = text[max(0, start - 12) : start]
    return any(hint in prefix for hint in EXTERNAL_HINTS)


def _to_int(value: str) -> int | None:
    if not value:
        return None
    if value.isdigit():
        return int(value)
    # 只处理常见的中文序数（一~九十九），更复杂的交给人工。
    if value == "十":
        return 10
    if value.startswith("十"):
        tail = CN_DIGITS.get(value[1:], 0) if len(value) > 1 else 0
        return 10 + tail
    if value.endswith("十"):
        head = CN_DIGITS.get(value[0])
        return head * 10 if head else None
    if len(value) == 1:
        return CN_DIGITS.get(value)
    return None


def extract_references(text: str) -> list[dict[str, Any]]:
    """抽取文本里的内部引用，返回 [{kind, target, raw, status}]。

    status 由调用方结合章节结构再判定，这里先统一给 unverifiable。
    """
    if not text:
        return []
    seen: set[tuple[str, str]] = set()
    references: list[dict[str, Any]] = []

    def add(kind: str, target: str, raw: str) -> None:
        key = (kind, target)
        if not target or key in seen:
            return
        seen.add(key)
        references.append(
            {
                "kind": kind,
                "target": target,
                "raw": raw[:80],
                "status": STATUS_UNVERIFIABLE,
            }
        )

    for match in SECTION_REF_PATTERN.finditer(text):
        if _is_external(text, match.start()):
            continue
        add("section", normalize_number(match.group(1)), match.group(0))
    for match in ATTACHMENT_REF_PATTERN.finditer(text):
        if _is_external(text, match.start()):
            continue
        number = normalize_number(match.group(2))
        add("attachment", f"{match.group(1)}{number}" if number else match.group(1), match.group(0))
    for match in DOCUMENT_REF_PATTERN.finditer(text):
        target = match.group(1).strip()
        # 《招标文件》《投标文件》这类泛指不构成证据引用。
        if target in {"招标文件", "投标文件", "采购文件"}:
            continue
        add("document", target, match.group(0))
    return references


def normalize_number(value: str | None) -> str:
    number = _to_int((value or "").strip())
    return str(number) if number is not None else (value or "").strip()


def check_references(
    text: str, section_orders: set[int]
) -> list[dict[str, Any]]:
    """判定引用状态：章节引用能对上顺序号才算 resolved，其余一律 unverifiable。"""
    references = extract_references(text)
    for reference in references:
        if reference["kind"] != "section":
            continue
        try:
            order = int(reference["target"])
        except (TypeError, ValueError):
            continue
        reference["status"] = (
            STATUS_RESOLVED if order in section_orders else STATUS_DANGLING
        )
    return references


def unresolved(references: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """只保留需要提醒模型的引用（悬空或无法核实）。"""
    return [
        reference
        for reference in references
        if reference["status"] in {STATUS_DANGLING, STATUS_UNVERIFIABLE}
    ]
