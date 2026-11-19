"""按评分项从投标章节里定向召回证据段落。

为什么不再整章塞给模型：一次评 30 个评分项，每章只能塞前 1500 字，技术方案的结论、
指标往往在后半段，模型从未看到 —— 分数是"没看完"得出的，而且静默发生。改成按评分项
召回后，每个评分项看到的是与其 item/criteria 最相关的段落，而不是所有章节的开头。

召回用确定性关键词打分（中文 bigram + IDF），不引入向量检索：
- 可复现、可解释：能说清"为什么这段被选中"；
- 零额外依赖、零调用成本；
- 口径与 knowledge/kb.py 的检索一致（同样的 bigram 切分与 IDF 思路）。

召回不到时的降级顺序由调用方决定：先回退该评分项绑定的章节，再退回"无证据"并告警。
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, replace
from typing import Any

WORD_PATTERN = re.compile(r"[a-zA-Z][a-zA-Z0-9_\-]{1,}|[0-9]{2,}")
CJK_PATTERN = re.compile(r"[\u4e00-\u9fff]+")

# 只滤虚词。名词类高频词（方案/文件/项目）交给 IDF 降权，直接滤掉会毁掉标题召回。
STOPWORDS = {
    "我们", "你们", "他们", "以及", "并且", "或者", "如果", "因为", "所以",
    "进行", "提供", "相关", "有关", "根据", "按照", "对于", "关于",
    "可以", "需要", "应当", "必须", "具有", "具备", "符合", "满足",
}

PARAGRAPH_MIN_CHARS = 40
PARAGRAPH_MAX_CHARS = 600
MAX_RECALL_PER_ITEM = 5
# 单个评分项送进模型的证据上限。30 项 × 2000 = 60000，正好等于总预算。
MAX_RECALL_CHARS = 2000
# 只保留与最高分同量级的段落，避免把"沾了一个字"的段落也塞进去。
MIN_SCORE_RATIO = 0.15
# 表格行数上限：业绩表动辄几十行，全送会挤掉其他评分项的证据预算。
TABLE_MAX_ROWS = 30


@dataclass(frozen=True)
class Paragraph:
    section_id: str
    section_title: str
    order_index: int
    text: str
    # paragraph / table / bullets / image / response_statement
    kind: str = "paragraph"
    # 证据强度：表格与清单是可核验的硬证据，正文只是论述。
    weight: float = 1.0
    # 段落在章节内的序号，用于证据回溯定位（"技术方案响应 · 第 3 段"）。
    index_in_section: int = 0


def tokens(text: str) -> list[str]:
    lowered = (text or "").lower()
    result = WORD_PATTERN.findall(lowered)
    for segment in CJK_PATTERN.findall(lowered):
        if len(segment) == 1:
            result.append(segment)
            continue
        result.extend(segment[index : index + 2] for index in range(len(segment) - 1))
    return [token for token in result if token not in STOPWORDS]


def _split_long(text: str) -> list[str]:
    chunks: list[str] = []
    remaining = text
    while len(remaining) > PARAGRAPH_MAX_CHARS:
        cut = remaining.rfind("。", 0, PARAGRAPH_MAX_CHARS)
        if cut < PARAGRAPH_MIN_CHARS:
            cut = PARAGRAPH_MAX_CHARS
        chunks.append(remaining[: cut + 1].strip())
        remaining = remaining[cut + 1 :].strip()
    if remaining:
        chunks.append(remaining)
    return chunks


def _split_text(text: str) -> list[str]:
    """按行切段，过短的相邻行合并，过长的按句号切。"""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    merged: list[str] = []
    buffer = ""
    for line in lines:
        if buffer and len(buffer) + len(line) < PARAGRAPH_MIN_CHARS:
            buffer = f"{buffer}\n{line}"
            continue
        if buffer:
            merged.append(buffer)
        buffer = line
    if buffer:
        merged.append(buffer)
    result: list[str] = []
    for block in merged:
        if len(block) <= PARAGRAPH_MAX_CHARS:
            result.append(block)
        else:
            result.extend(_split_long(block))
    return [block for block in result if block.strip()]


def split_sections(sections: list[Any]) -> list[Paragraph]:
    paragraphs: list[Paragraph] = []
    for section in sections:
        paragraphs.extend(split_section_text(section))
    return paragraphs


def _paragraph(section: Any, text: str, kind: str, weight: float) -> Paragraph:
    return Paragraph(
        section_id=str(section.id),
        section_title=section.title,
        order_index=section.order_index,
        text=text,
        kind=kind,
        weight=weight,
    )


def _render_table(block: dict[str, Any]) -> str:
    """把表格渲染成带表头的文本。

    content_text 走 _blocks_text 时只把每行单元格用空格拼起来、**丢掉了表头**，
    业绩表/人员表这类同名列一多就分不清哪列是什么。这里保留表头与列关系。
    """
    lines: list[str] = []
    caption = str(block.get("caption") or "").strip()
    if caption:
        lines.append(f"表：{caption}")
    headers = [str(header).strip() for header in block.get("headers") or []]
    if headers:
        lines.append("| " + " | ".join(headers) + " |")
    rows = block.get("rows") or []
    rendered = 0
    for row in rows[:TABLE_MAX_ROWS]:
        cells = [str(cell).strip() for cell in row]
        if not any(cells):
            continue
        lines.append("| " + " | ".join(cells) + " |")
        rendered += 1
    if rendered:
        # 行数由代码给出，不让模型数——客观分（业绩份数、人员数量）靠它才数得准。
        note = f"（本表共 {len(rows)} 行数据"
        if len(rows) > rendered:
            note += f"，此处仅展示前 {rendered} 行"
        lines.append(note + "）")
    return "\n".join(lines)


def _blocks_to_paragraphs(section: Any, blocks: list[Any]) -> list[Paragraph]:
    result: list[Paragraph] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        block_type = str(block.get("type") or "paragraph")
        if block_type == "table":
            text = _render_table(block)
            if text:
                result.append(_paragraph(section, text, "table", 1.5))
        elif block_type == "bullets":
            items = [str(item).strip() for item in block.get("items") or []]
            items = [item for item in items if item]
            if items:
                for chunk in _split_long("\n".join(f"- {item}" for item in items)):
                    result.append(_paragraph(section, chunk, "bullets", 1.2))
        elif block_type == "image":
            caption = str(block.get("caption") or "").strip()
            if caption:
                result.append(_paragraph(section, f"图：{caption}", "image", 0.8))
        else:
            for text in _split_text(str(block.get("text") or "")):
                result.append(_paragraph(section, text, "paragraph", 1.0))
    return result


RESPONSE_PATTERN = re.compile(
    # 只认"我方/投标人 + 完全响应"这类正式声明，不能见到"满足"就标：
    # 方案正文里"满足高可用要求"是论证，不是响应声明。
    r"(我方|本公司|投标人)[^。；]{0,20}(完全|全部|均)?\s*(响应|满足|符合)"
    r"|(完全|全部)响应|无(负)?偏离|均无偏离"
)


def _mark_response_statements(paragraphs: list[Paragraph], limit: int = 3) -> list[Paragraph]:
    """标题章开头的"完全响应/无偏离"是判断响应的第一信号，单独标出来。

    只打标记、不重排：头段本来就是第一段，重排会打乱 select_within_budget 依赖的
    首尾结构。
    """
    marked: list[Paragraph] = []
    for index, paragraph in enumerate(paragraphs):
        if (
            index < limit
            and paragraph.kind == "paragraph"
            and RESPONSE_PATTERN.search(paragraph.text)
        ):
            marked.append(replace(paragraph, kind="response_statement"))
        else:
            marked.append(paragraph)
    return marked


def _numbered(paragraphs: list[Paragraph]) -> list[Paragraph]:
    """统一给段落编号：位置回溯要靠它。"""
    return [
        replace(paragraph, index_in_section=index)
        for index, paragraph in enumerate(paragraphs)
    ]


def split_section_text(section: Any) -> list[Paragraph]:
    """优先用 content_json 的结构化块（表格保表头），退回 content_text 纯文本切分。"""
    blocks = getattr(section, "content_json", None) or []
    if blocks:
        paragraphs = _blocks_to_paragraphs(section, blocks)
        if paragraphs:
            return _numbered(_mark_response_statements(paragraphs))
    raw = (section.content_text or "").strip()
    if not raw:
        return []
    return _numbered(
        _mark_response_statements(
            [_paragraph(section, text, "paragraph", 1.0) for text in _split_text(raw)]
        )
    )


def _query_text(item: Any) -> str:
    return " ".join(
        part for part in (item.item, item.criteria, item.category) if part and str(part).strip()
    )


ITEM_TYPE_OBJECTIVE = "objective"
ITEM_TYPE_QUALIFICATION = "qualification"
ITEM_TYPE_SUBJECTIVE = "subjective"

# 只有明确的实质性条款才走"响应/不响应"判定；"资质证书"这类仍按数量计分。
QUALIFICATION_PATTERN = re.compile(
    r"★|▲|实质性(要求|响应|条款)|否决|废标|无效投标|资格预审|准入条件"
)
# 客观分靠"数得清"：业绩份数、人员数量、证书等级、注册资本。
OBJECTIVE_PATTERN = re.compile(
    r"每(提供|增加|具备|配置)|不少于|至少|不低于|\d+\s*(个|份|项|人|台|套)|"
    r"业绩|合同|中标通知|验收|证书|资质|职称|学历|专利|软著|著作权|人员配置|注册资本"
)


def classify_item(item: Any) -> str:
    """按评分项文本判定类型，决定证据装配与提示词策略。

    启发式而非模型判断：分错只影响"按什么方式提示模型评分"，不直接改分数，
    结果写进 meta 供人工核查。
    """
    text = " ".join(str(part) for part in (item.item, item.criteria) if part)
    if QUALIFICATION_PATTERN.search(text):
        return ITEM_TYPE_QUALIFICATION
    if OBJECTIVE_PATTERN.search(text):
        return ITEM_TYPE_OBJECTIVE
    return ITEM_TYPE_SUBJECTIVE


def recall_for_items(
    items: list[Any],
    paragraphs: list[Paragraph],
    *,
    per_item: int = MAX_RECALL_PER_ITEM,
    max_chars: int = MAX_RECALL_CHARS,
) -> dict[str, list[dict[str, Any]]]:
    """返回 {scoring_item_id: [{section, text, score}]}，按相关度降序。"""
    if not paragraphs or not items:
        return {str(item.id): [] for item in items}
    para_tokens: list[set[str]] = []
    document_frequency: Counter[str] = Counter()
    for paragraph in paragraphs:
        token_set = set(tokens(paragraph.text))
        para_tokens.append(token_set)
        document_frequency.update(token_set)
    total = len(paragraphs)
    idf = {
        token: math.log((total + 1) / (frequency + 1)) + 1
        for token, frequency in document_frequency.items()
    }

    result: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        query_tokens = set(tokens(_query_text(item)))
        if not query_tokens:
            result[str(item.id)] = []
            continue
        scored: list[tuple[float, Paragraph]] = []
        for paragraph, token_set in zip(paragraphs, para_tokens, strict=True):
            hits = query_tokens & token_set
            if not hits:
                continue
            score = sum(idf.get(token, 1.0) for token in hits)
            # 章节标题命中说明整段主题对得上，比正文偶发命中更可信。
            title_hits = query_tokens & set(tokens(paragraph.section_title))
            if title_hits:
                score += sum(idf.get(token, 1.0) for token in title_hits) * 1.5
            scored.append((score, paragraph))
        if not scored:
            result[str(item.id)] = []
            continue
        scored.sort(key=lambda entry: entry[0], reverse=True)
        floor = scored[0][0] * MIN_SCORE_RATIO
        picked: list[dict[str, Any]] = []
        used_chars = 0
        for score, paragraph in scored:
            if len(picked) >= per_item or score < floor:
                break
            if picked and used_chars + len(paragraph.text) > max_chars:
                break
            picked.append(
                {
                    "section_id": paragraph.section_id,
                    "section": paragraph.section_title,
                    "text": paragraph.text[:max_chars],
                    "score": round(score, 3),
                    "kind": paragraph.kind,
                    "paragraph_index": paragraph.index_in_section,
                }
            )
            used_chars += len(paragraph.text)
        result[str(item.id)] = picked
    return result


def _as_entry(paragraph: Paragraph, score: float) -> dict[str, Any]:
    return {
        "section_id": paragraph.section_id,
        "section": paragraph.section_title,
        "text": paragraph.text,
        "score": score,
        # 让模型知道这是表格/清单/正文：表格是可核验的硬证据，论述只是说明。
        "kind": paragraph.kind,
        # 复核时能定位到章节内第几段。
        "paragraph_index": paragraph.index_in_section,
    }


def _score_paragraphs(
    query_tokens: set[str], paragraphs: list[Paragraph]
) -> list[tuple[int, float]]:
    scored: list[tuple[int, float]] = []
    for index, paragraph in enumerate(paragraphs):
        hits = query_tokens & set(tokens(paragraph.text))
        if hits:
            # 表格与清单按证据强度加权：命中同样多时，优先送可核验的结构化证据。
            scored.append((index, len(hits) * paragraph.weight))
    scored.sort(key=lambda entry: entry[1], reverse=True)
    return scored


def select_within_budget(
    paragraphs: list[Paragraph],
    query: str,
    budget: int,
) -> tuple[list[dict[str, Any]], bool]:
    """在字数预算内挑出章节段落，返回 (段落, 是否发生截断)。

    绑定章节是"为这个评分项写的正文"，放得下就整章送 —— 这是最准确的做法，不依赖
    任何同义词匹配。放不下时先保头尾（概述与结论通常在两端），剩余预算给中间最相关
    的段落。这样不会再出现"只取前 1500 字、结论永远看不到"。
    """
    if not paragraphs:
        return [], False
    if budget <= 0:
        return [], True
    if sum(len(paragraph.text) for paragraph in paragraphs) <= budget:
        return [_as_entry(paragraph, 0.0) for paragraph in paragraphs], False

    query_tokens = set(tokens(query))
    scored = _score_paragraphs(query_tokens, paragraphs)
    selected: list[int] = []
    used = 0
    # 头尾优先：开头是概述（常含响应声明），结尾是结论/承诺，中间才是可裁剪的展开论证。
    for index in (0, len(paragraphs) - 1):
        if index < 0 or index in selected:
            continue
        text = paragraphs[index].text
        if used + len(text) > budget:
            continue
        selected.append(index)
        used += len(text)
    # 表格与清单是可核验的硬证据（业绩、人员、参数），即使关键词没命中也优先占位；
    # 但最多占预算六成，避免一张长业绩表把解读性正文全挤掉。
    table_cap = int(budget * 0.6)
    table_used = 0
    for index, paragraph in enumerate(paragraphs):
        if index in selected or paragraph.kind not in ("table", "bullets"):
            continue
        text = paragraph.text
        if table_used + len(text) > table_cap or used + len(text) > budget:
            continue
        selected.append(index)
        used += len(text)
        table_used += len(text)
    for index, score in scored:
        if index in selected or score <= 0:
            continue
        text = paragraphs[index].text
        if used + len(text) > budget:
            continue
        selected.append(index)
        used += len(text)
    if not selected:
        # 单段就超预算：至少把头段截断送出，不能返回空让评分项变成"无证据"。
        head = paragraphs[0]
        return [
            {
                "section_id": head.section_id,
                "section": head.section_title,
                "text": head.text[:budget],
                "score": 0.0,
            }
        ], True
    # 输出保持原文顺序，段落乱序会让模型误判上下文关系。
    selected.sort()
    return [_as_entry(paragraphs[index], 0.0) for index in selected], True
