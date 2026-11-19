import io
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from docx import Document as DocxDocument
from docx.table import Table
from docx.text.paragraph import Paragraph
from pypdf import PdfReader

from app.modules.tender.bid_risks import extract_bid_risks
from app.modules.tender.packages import extract_packages

PARSER_VERSION = "rules-v1.3"
SUPPORTED_SUFFIXES = {".docx", ".pdf", ".txt", ".md"}

REJECT_KEYWORDS = (
    "废标",
    "否决",
    "无效投标",
    "投标无效",
    "不予受理",
    "拒绝接收",
    "取消投标资格",
    "视为放弃",
)
REJECT_LEAD_IN = re.compile(r"(下列|以下|有下列情形之一的|存在下列情形之一)")
LIST_ITEM = re.compile(r"^\s*(?:[(（]?[0-9一二三四五六七八九十]+[)）、.．]\s*)\S")
MD_HEADING = re.compile(r"^(#{1,6})\s+(.+)$")
CLAUSE_HEADER = re.compile(r"[：:]\s*$")
NEGATIVE_QUALIFICATION = re.compile(r"(不具备|未通过|未按|未按要求|未提供|缺少)")
TABLE_CAPTION = re.compile(r"^\s*(附表|附件|续表|表)\s*\d*\s*[：:.、]?\s*[^0-9]*$")
QUALIFICATION_KEYWORDS = (
    "资质",
    "资格",
    "证书",
    "许可证",
    "认证",
    "授权书",
    "人员",
    "项目经理",
    "工程师",
    "持证",
    "业绩",
    "案例",
    "合同",
    "财务",
    "审计",
    "信用",
    "失信",
    "违法",
    "营业执照",
)
FORMAT_KEYWORDS = ("字体", "字号", "行距", "页边距", "装订", "签章", "盖章", "页码", "目录", "封面", "A4")
TIMELINE_EVENTS = ("投标截止", "开标", "报名", "答疑", "递交", "获取招标文件", "保证金")
CST = timezone(timedelta(hours=8))
DEADLINE_EVENTS = ("投标截止", "递交")
CHECKLIST_KEYWORDS = ("投标文件组成", "投标文件应包括", "材料清单", "须提交", "应提交", "投标文件包含")
CLARIFICATION_KEYWORDS = ("澄清", "答疑", "修改", "补充通知", "变更公告")
CLARIFICATION_CHANGE = re.compile(r"(修改为|调整为|变更为|更改为|改为)")
SCORING_LINE = re.compile(r"^[^。；]{2,50}[：:]\s*\d+(?:\.\d+)?\s*分")
SCORING_ITEM_REJECT = ("由", "调整为", "修改为", "变更", "更正", "不低于", "不少于", "达到")
# 预算抽取的排除词：命中窗口内出现这些词说明是门槛/说明性表述，不是预算金额。
# 注意不要放「不含/不含税/以下」这类常见修饰：真实公告里「预算金额：100万元（不含税）」
# 「最高限价：200万元以下」都是有效预算，误伤代价远大于漏判。
BUDGET_NEGATION_TOKENS = (
    "未达",
    "未超过",
    "不足",
    "低于",
    "不少于",
    "不低于",
)
LIST_PREFIX = re.compile(r"^\s*(?:[0-9]+[.、．)]|[-•*])\s*")
DARK_BID_LINE = re.compile(r"不得[^。；]{0,8}(公司|投标人|供应商|单位|企业|作者)[^。；]{0,2}名称")


class UnsupportedDocumentError(Exception):
    pass


class ScannedDocumentError(Exception):
    pass


@dataclass(frozen=True)
class Block:
    index: int
    text: str
    kind: str = "paragraph"
    page: int | None = None
    table_index: int | None = None
    row: int | None = None
    col: int | None = None
    header: bool = False


@dataclass
class ParsedDocument:
    blocks: list[Block]
    warnings: list[str] = field(default_factory=list)
    page_count: int | None = None

    @property
    def text(self) -> str:
        return "\n".join(block.text for block in self.blocks)


def parse_document(content: bytes, filename: str) -> ParsedDocument:
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise UnsupportedDocumentError(f"unsupported document type: {suffix}")
    if suffix == ".docx":
        return _parse_docx(content)
    if suffix == ".pdf":
        return _parse_pdf(content)
    return _parse_text(content)


def _parse_docx(content: bytes) -> ParsedDocument:
    document = DocxDocument(io.BytesIO(content))
    blocks: list[Block] = []
    table_index = 0
    for element in document.element.body.iterchildren():
        if element.tag.endswith("}p"):
            paragraph = Paragraph(element, document)
            text = paragraph.text.strip()
            if text:
                style = paragraph.style
                style_name = style.name if style is not None else ""
                kind = "heading" if style_name.startswith(("Heading", "Title")) else "paragraph"
                blocks.append(Block(index=len(blocks), text=text, kind=kind))
        elif element.tag.endswith("}tbl"):
            table = Table(element, document)
            for row_index, row in enumerate(table.rows):
                for col_index, cell in enumerate(row.cells):
                    text = cell.text.strip()
                    if text:
                        blocks.append(
                            Block(
                                index=len(blocks),
                                text=text,
                                kind="table_cell",
                                table_index=table_index,
                                row=row_index,
                                col=col_index,
                                header=row_index == 0,
                            )
                        )
            table_index += 1
    return ParsedDocument(blocks=blocks)


def _parse_pdf_text_fallback(content: bytes) -> ParsedDocument:
    reader = PdfReader(io.BytesIO(content))
    blocks: list[Block] = []
    for page_number, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        for line in text.splitlines():
            if line.strip():
                blocks.append(
                    Block(index=len(blocks), text=line.strip(), kind="paragraph", page=page_number)
                )
    if not blocks:
        raise ScannedDocumentError("scanned_pdf_requires_ocr")
    return ParsedDocument(blocks=blocks, page_count=len(reader.pages))


def _parse_pdf(content: bytes) -> ParsedDocument:
    """文字层 PDF：pdfplumber 为主（段落 + 表格 cell 块，与 docx 同形），
    解析异常回退 pypdf 纯文本并记 warning；无文本层仍交 OCR。"""
    try:
        import pdfplumber
    except ImportError as exc:  # 依赖缺失要诚实抛，不静默降级
        raise UnsupportedDocumentError(f"pdfplumber is required for PDF parsing: {exc}") from exc
    try:
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            blocks: list[Block] = []
            table_index = 0
            page_count = len(pdf.pages)
            for page_number, page in enumerate(pdf.pages, start=1):
                table_bboxes: list[tuple[float, float, float, float]] = []
                for table in page.find_tables():
                    rows = table.extract()
                    table_bboxes.append(table.bbox)
                    for row_no, row in enumerate(rows):
                        for col_no, cell in enumerate(row):
                            cell_text = (cell or "").strip()
                            if not cell_text:
                                continue
                            blocks.append(
                                Block(
                                    index=len(blocks),
                                    text=cell_text,
                                    kind="table_cell",
                                    page=page_number,
                                    table_index=table_index,
                                    row=row_no,
                                    col=col_no,
                                    header=(row_no == 0),
                                )
                            )
                    table_index += 1
                # 段落行按词坐标聚合；落在表格 bbox 内的行跳过（内容已按 cell 入库）
                lines_by_top: dict[int, list[dict]] = defaultdict(list)
                for word in page.extract_words():
                    lines_by_top[round(word["top"] / 3)].append(word)
                for _, words in sorted(lines_by_top.items()):
                    text = " ".join(
                        word["text"] for word in sorted(words, key=lambda item: item["x0"])
                    ).strip()
                    if not text:
                        continue
                    mid_x = (min(w["x0"] for w in words) + max(w["x1"] for w in words)) / 2
                    mid_y = (min(w["top"] for w in words) + max(w["bottom"] for w in words)) / 2
                    if any(
                        bbox[0] <= mid_x <= bbox[2] and bbox[1] <= mid_y <= bbox[3]
                        for bbox in table_bboxes
                    ):
                        continue
                    blocks.append(
                        Block(index=len(blocks), text=text, kind="paragraph", page=page_number)
                    )
            if not blocks:
                raise ScannedDocumentError("scanned_pdf_requires_ocr")
            return ParsedDocument(blocks=blocks, page_count=page_count)
    except ScannedDocumentError:
        raise
    except Exception as exc:  # pdfplumber 解析失败：回退纯文本路径
        fallback = _parse_pdf_text_fallback(content)
        fallback.warnings.append(f"pdf_fallback: {type(exc).__name__}")
        return fallback


def _parse_text(content: bytes) -> ParsedDocument:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("gb18030")
    blocks: list[Block] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        heading = MD_HEADING.match(line)
        if heading:
            title = heading.group(2).strip()
            if title:
                blocks.append(Block(index=len(blocks), text=title, kind="heading"))
            continue
        blocks.append(Block(index=len(blocks), text=line, kind="paragraph"))
    return ParsedDocument(blocks=blocks)


def _source_span(block: Block) -> dict[str, Any]:
    return {
        "block_index": block.index,
        "page": block.page,
        "kind": block.kind,
        "table_index": block.table_index,
        "row": block.row,
        "col": block.col,
        "quote": block.text[:240],
    }


def _parse_score(value: str | None) -> Decimal | None:
    if not value:
        return None
    matched = re.search(r"(\d+(?:\.\d+)?)", value.replace(",", ""))
    return Decimal(matched.group(1)) if matched else None


SCORE_HEADER_ALIASES = ("分值", "分数", "得分", "权重")
ITEM_HEADER_ALIASES = (
    "评分项",
    "评审因素",
    "评审项目",
    "评审内容",
    "评分内容",
    "评标因素",
    "评分因素",
    "评审项",
    "项目",
)
HEADER_SCAN_ROWS = 5
HEADER_CELL_MAX_CHARS = 12


def _looks_like_header_row(header: dict[int, str]) -> bool:
    """表头行是短标签；数据行里常出现「（分值：5分…」这类长句，不能当表头。"""
    texts = [text.strip() for text in header.values() if text and text.strip()]
    if len(texts) < 2:
        return False
    return all(
        len(text) <= HEADER_CELL_MAX_CHARS and "：" not in text and "。" not in text
        for text in texts
    )


def _find_scoring_header(
    rows: dict[int, dict[int, Block]],
) -> tuple[int, dict[int, str]] | None:
    ordered = sorted(rows)
    # 先看前几行（绝大多数表格表头都在顶部）
    for row_index in ordered[:HEADER_SCAN_ROWS]:
        header = {col: block.text for col, block in rows[row_index].items()}
        if not _looks_like_header_row(header):
            continue
        has_score = any(any(alias in text for alias in SCORE_HEADER_ALIASES) for text in header.values())
        has_item = any(any(alias in text for alias in ITEM_HEADER_ALIASES) for text in header.values())
        if has_score and has_item:
            return row_index, header
    # 兜底：真实公告常有「标题行 + 编制说明行」把表头压到 5 行之后。
    # 仍要求命中评分/项目别名，且单元格必须是短标签，避免把数据行当表头。
    for row_index in ordered[HEADER_SCAN_ROWS:]:
        header = {col: block.text for col, block in rows[row_index].items()}
        if not _looks_like_header_row(header):
            continue
        has_score = any(any(alias in text for alias in SCORE_HEADER_ALIASES) for text in header.values())
        has_item = any(any(alias in text for alias in ITEM_HEADER_ALIASES) for text in header.values())
        if has_score and has_item:
            return row_index, header
    return None


def _infer_category(text: str) -> str:
    if any(keyword in text for keyword in ("报价", "价格", "投标报价")):
        return "价格"
    if any(keyword in text for keyword in ("技术", "方案", "实施", "服务")):
        return "技术"
    if any(keyword in text for keyword in ("商务", "业绩", "资质", "人员")):
        return "商务"
    return "其他"


def _scoring_table_indexes(blocks: list[Block]) -> set[int]:
    tables: dict[int, list[Block]] = defaultdict(list)
    for block in blocks:
        if block.kind == "table_cell" and block.table_index is not None:
            tables[block.table_index].append(block)
    indexes: set[int] = set()
    for table_index, table_blocks in tables.items():
        rows: dict[int, dict[int, Block]] = defaultdict(dict)
        for block in table_blocks:
            if block.row is not None and block.col is not None:
                rows[block.row][block.col] = block
        if _find_scoring_header(rows) is not None:
            indexes.add(table_index)
    return indexes


def _qualification_category(text: str) -> str:
    if any(keyword in text for keyword in ("人员", "项目经理", "工程师", "持证")):
        return "PERSONNEL"
    if any(keyword in text for keyword in ("业绩", "案例", "合同", "项目经验")):
        return "PERFORMANCE"
    if any(keyword in text for keyword in ("财务", "审计", "资产负债", "营收")):
        return "FINANCE"
    if any(keyword in text for keyword in ("信用", "失信", "违法", "裁判")):
        return "CREDIT"
    if any(keyword in text for keyword in ("资质", "资格", "认证", "营业执照", "许可证", "授权书")):
        return "QUALIFICATION"
    return "OTHER"


def _clean_scoring_item(text: str) -> str:
    return LIST_PREFIX.sub("", text.strip(" ，,、")).strip()


def _criteria_label(criteria: str | None) -> str:
    """从评审细则里取短标签（首个冒号前的部分），用于区分同名评分项。"""
    if not criteria:
        return ""
    text = criteria.strip()
    head = re.split(r"[：:]", text, maxsplit=1)[0].strip()
    if head and head != text and len(head) <= 30:
        return head
    clause = re.split(r"[，。；、,;]", text, maxsplit=1)[0].strip()
    return (clause or text)[:14]


def _extract_scoring(blocks: list[Block]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    tables: dict[int, list[Block]] = defaultdict(list)
    for block in blocks:
        if block.kind == "table_cell" and block.table_index is not None:
            tables[block.table_index].append(block)

    for table_blocks in tables.values():
        rows: dict[int, dict[int, Block]] = defaultdict(dict)
        for block in table_blocks:
            if block.row is not None and block.col is not None:
                rows[block.row][block.col] = block
        if not rows:
            continue
        matched_header = _find_scoring_header(rows)
        header_row = matched_header[0] if matched_header is not None else 0
        header = (
            matched_header[1]
            if matched_header is not None
            else {col: block.text for col, block in rows.get(0, {}).items()}
        )

        def find_col(aliases: tuple[str, ...], header: dict[int, str] = header) -> int | None:
            for col, text in header.items():
                if any(alias in text for alias in aliases):
                    return col
            return None

        item_col = find_col(
            ("评分项", "评审因素", "评审项目", "评分内容", "评审内容", "评标因素", "评分因素", "评标方法", "评审项", "项目")
        )
        score_col = find_col(("分值", "分数", "得分", "权重"))
        criteria_col = find_col(
            ("评分标准", "评审标准", "评分细则", "评审细则", "评审要素", "标准", "评审说明")
        )
        category_col = find_col(("类别", "评分类型", "评分因素"))
        if score_col is None:
            continue
        table_rows: list[dict[str, Any]] = []
        seen_pairs: set[str] = set()
        for row_index, cells in sorted(rows.items()):
            if row_index <= header_row:
                continue
            item_block = cells.get(item_col) if item_col is not None else None
            if item_block is None:
                candidates = [
                    block
                    for col, block in cells.items()
                    if block.text and col != score_col and col != criteria_col
                ]
                item_block = max(candidates, key=lambda block: len(block.text), default=None)
            score = _parse_score(cells[score_col].text if score_col in cells else None)
            if item_block is None or score is None:
                continue
            item = item_block.text.strip()
            if item.isdigit():
                continue
            if not re.sub(r"\s+", "", item) or "合计" in item:
                continue
            criteria = (
                cells[criteria_col].text.strip()
                if criteria_col is not None and criteria_col in cells
                else None
            )
            label = _criteria_label(criteria)
            pair_key = re.sub(r"\s+", "", f"{item}|{label}")
            if not pair_key or pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)
            category_text = (
                cells[category_col].text.strip()
                if category_col is not None and category_col in cells
                else f"{item} {criteria or ''}"
            )
            table_rows.append(
                {
                    "item": item,
                    "label": label,
                    "score": score,
                    "criteria": criteria,
                    "category_text": category_text,
                    "block": item_block,
                }
            )
        # 合并单元格会让多行共用同一个评审内容（如「投标供应商履约能力评价」×6），
        # 这类行必须按评审细则拆成多条，否则只保留第一条。
        counts = Counter(row["item"] for row in table_rows)
        for row in table_rows:
            item = row["item"]
            if counts[item] > 1 and row["label"]:
                item = f"{item}｜{row['label']}"
            key = re.sub(r"\s+", "", item)
            if key in seen or key in {"合计", "总计", "小计", "总分", "满分"}:
                continue
            seen.add(key)
            items.append(
                {
                    "category": _infer_category(row["category_text"]),
                    "item": item,
                    "score": float(row["score"]),
                    "criteria": row["criteria"],
                    "source_span": _source_span(row["block"]),
                    "confidence": "high",
                }
            )

    if not items:
        pattern = re.compile(
            r"(?P<item>[^\n。；;：:]{2,50}?)[：: ]{0,3}(?P<score>\d+(?:\.\d+)?)\s*分"
        )
        for block in blocks:
            for match in pattern.finditer(block.text):
                item = _clean_scoring_item(match.group("item"))
                if any(token in item for token in SCORING_ITEM_REJECT):
                    continue
                score = Decimal(match.group("score"))
                key = re.sub(r"\s+", "", item)
                if not key or key in seen or key in {"得分", "满分", "总分", "合计", "总计", "小计"}:
                    continue
                seen.add(key)
                items.append(
                    {
                        "category": _infer_category(item),
                        "item": item,
                        "score": float(score),
                        "criteria": block.text[:300],
                        "source_span": _source_span(block),
                        "confidence": "medium",
                    }
                )
    return items[:200]


def _extract_qualifications(blocks: list[Block]) -> list[dict[str, Any]]:
    requirements: list[dict[str, Any]] = []
    seen: set[str] = set()
    scoring_tables = _scoring_table_indexes(blocks)
    for block in blocks:
        text = block.text
        if block.kind == "heading":
            continue
        if block.kind == "table_cell" and block.table_index in scoring_tables:
            continue
        if any(keyword in text for keyword in REJECT_KEYWORDS):
            continue
        if any(keyword in text for keyword in CHECKLIST_KEYWORDS):
            continue
        if SCORING_LINE.match(text):
            continue
        if REJECT_LEAD_IN.search(text) is not None or NEGATIVE_QUALIFICATION.search(text):
            continue
        if TABLE_CAPTION.match(text):
            continue
        if block.kind == "table_cell" and not any(
            keyword in text for keyword in ("须", "应", "不得", "不少于", "至少", "具备", "具有", "提供")
        ):
            continue
        if not any(keyword in text for keyword in QUALIFICATION_KEYWORDS):
            continue
        key = re.sub(r"\s+", "", text)
        if key in seen:
            continue
        seen.add(key)
        level_match = re.search(r"(甲级|乙级|丙级|一级|二级|三级|四级|A级|B级|C级)", text)
        quantity_match = re.search(r"(不少于|至少|不低于)\s*(\d+)\s*(个|份|名|人|项)", text)
        requirements.append(
            {
                "category": _qualification_category(text),
                "text": text[:500],
                "name": text[:200],
                "level": level_match.group(1) if level_match else None,
                "quantity": int(quantity_match.group(2)) if quantity_match else None,
                "source_span": _source_span(block),
                "confidence": "medium",
            }
        )
    return requirements[:100]


def _extract_reject_clauses(blocks: list[Block]) -> list[dict[str, Any]]:
    clauses: list[dict[str, Any]] = []
    seen: set[str] = set()
    in_list = False
    for block in blocks:
        text = block.text
        triggered = any(keyword in text for keyword in REJECT_KEYWORDS)
        if in_list and not LIST_ITEM.match(text):
            in_list = False
        if REJECT_LEAD_IN.search(text) is not None and triggered:
            in_list = True
            if CLAUSE_HEADER.search(text):
                continue
        candidate = text if (in_list or triggered) else None
        if not candidate:
            continue
        key = re.sub(r"\s+", "", candidate)
        if key in seen:
            continue
        seen.add(key)
        clauses.append(
            {
                "text": candidate[:500],
                "source_span": _source_span(block),
                "severity": "HARD",
                "confidence": "high",
            }
        )
    return clauses[:100]


def _extract_bid_validity_days(blocks: list[Block]) -> int | None:
    """只接受“投标有效期 + 明确天数”，不接受保证金有效期或模糊表述。"""
    day = re.compile(r"投标有效期[^。；\n]{0,30}?(\d+)\s*(?:个)?(?:日历)?天")
    day_alt = re.compile(r"投标有效期[^。；\n]{0,30}?(\d+)\s*日")
    for block in blocks:
        if "投标有效期" not in block.text or "保证金" in block.text:
            continue
        match = day.search(block.text) or day_alt.search(block.text)
        if match:
            days = int(match.group(1))
            if 1 <= days <= 3650:
                return days
    return None


def _extract_front_appendix(blocks: list[Block]) -> list[dict[str, Any]]:
    """投标人须知前附表是实质性条款最密集的区域，保留原文供逐条核对。"""
    items: list[dict[str, Any]] = []
    collecting = False
    for block in blocks:
        text = block.text.strip()
        if "前附表" in text:
            collecting = True
        if not collecting:
            continue
        if items and block.kind == "heading" and len(items) >= 3:
            break
        items.append(
            {
                "text": text[:500],
                "source_span": _source_span(block),
                "confidence": "medium",
            }
        )
        if len(items) >= 80:
            break
    return items


TECH_PARAM_MARKERS = (
    "参数",
    "指标",
    "性能",
    "可用性",
    "并发",
    "吞吐",
    "容量",
    "响应时间",
    "接口",
    "协议",
    "架构",
    "部署",
    "支持",
    "兼容",
    "不低于",
    "≥",
    "≤",
)


def _extract_technical_parameters(blocks: list[Block]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for block in blocks:
        if block.kind == "heading":
            continue
        text = block.text.strip()
        if len(text) < 6 or len(text) > 500:
            continue
        if not any(marker in text for marker in TECH_PARAM_MARKERS):
            continue
        if not re.search(r"[0-9]", text) and not any(
            marker in text for marker in ("支持", "兼容", "接口", "协议")
        ):
            continue
        key = re.sub(r"\s+", "", text)
        if key in seen:
            continue
        seen.add(key)
        items.append(
            {
                "name": text[:80],
                "text": text,
                "source_span": _source_span(block),
                "confidence": "medium",
            }
        )
    return items[:100]


def _extract_star_clauses(blocks: list[Block]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for block in blocks:
        text = block.text.strip()
        if not text:
            continue
        marker = "★" if "★" in text else "▲" if "▲" in text else None
        if marker is None and not any(
            keyword in text for keyword in ("实质性条款", "实质性要求")
        ):
            continue
        items.append(
            {
                "text": text[:500],
                "marker": marker or "实质性",
                "source_span": _source_span(block),
                "confidence": "medium",
            }
        )
    return items[:100]


def _extract_deposit(blocks: list[Block]) -> dict[str, Any]:
    amount_re = re.compile(r"投标保证金[^。；\n]{0,40}?([0-9][0-9,，.]*)\s*(万元|元)")
    symbol_re = re.compile(r"投标保证金[^。；\n]{0,40}?[¥￥]\s*([0-9][0-9,，.]*)")
    validity_re = re.compile(
        r"(?:投标)?保证金[^。；\n]{0,50}?有效期[^。；\n]{0,20}?(\d+)\s*(?:个)?(?:日历)?天"
    )
    for block in blocks:
        if "保证金" not in block.text:
            continue
        amount: Decimal | None = None
        match = amount_re.search(block.text)
        if match:
            amount = Decimal(match.group(1).replace(",", "").replace("，", ""))
            if match.group(2) == "万元":
                amount *= 10000
        if amount is None:
            match = symbol_re.search(block.text)
            if match:
                amount = Decimal(match.group(1).replace(",", "").replace("，", ""))
        validity = validity_re.search(block.text)
        if amount is None and validity is None:
            continue
        return {
            "amount": str(amount) if amount is not None else None,
            "currency": "CNY",
            "validity_days": int(validity.group(1)) if validity else None,
            "text": block.text[:500],
            "source_span": _source_span(block),
            "confidence": "medium",
        }
    return {
        "amount": None,
        "currency": "CNY",
        "validity_days": None,
        "text": None,
        "source_span": None,
    }


def _extract_format_rules(blocks: list[Block]) -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = []
    for block in blocks:
        target = next((keyword for keyword in FORMAT_KEYWORDS if keyword in block.text), None)
        if target is None:
            continue
        rules.append(
            {
                "target": target,
                "expect": {"text": block.text[:300]},
                "source_span": _source_span(block),
                "confidence": "medium",
            }
        )
    return rules[:100]


ABSOLUTE_DATE = re.compile(r"(20\d{2})\s*[-年/]\s*(\d{1,2})\s*[-月/]\s*(\d{1,2})\s*日?")
# 「09:00」「9时30分」「上午9时」「下午3点」——中文公告里这四种混用
CLOCK_TIME = re.compile(
    r"(上午|下午|中午|晚上)?\s*(\d{1,2})\s*(?:[:：点时])\s*(\d{1,2})?\s*分?"
)
# 相对表述：「自公告发布之日起 5 个工作日」「公告发布后 3 个日历日」「7天」
RELATIVE_OFFSET = re.compile(
    r"(?:自|从)?\s*(?:公告|招标文件|采购文件)?\s*(发布|发出|公布|获取|下载|上网)?"
    r"[^。；;]{0,6}?(?:之日起|起|后|之内|以内|内)?\s*(\d{1,3})\s*个?\s*"
    r"(工作日|日历日|日历天|自然日|天|日|周)"
)
WORKDAY_HOLIDAYS = "2025-01-01|2025-01-28|2025-01-29|2025-01-30|2025-01-31|2025-02-01|" \
    "2025-02-02|2025-02-03|2025-02-04|2025-04-04|2025-05-01|2025-05-02|2025-05-03|" \
    "2025-05-04|2025-05-05|2025-05-31|2025-06-01|2025-06-02|2025-10-01|2025-10-02|" \
    "2025-10-03|2025-10-04|2025-10-05|2025-10-06|2025-10-07|2025-10-08|" \
    "2026-01-01|2026-01-02|2026-01-03|2026-02-15|2026-02-16|2026-02-17|2026-02-18|" \
    "2026-02-19|2026-02-20|2026-02-21|2026-02-22|2026-02-23|2026-04-04|2026-04-05|" \
    "2026-04-06|2026-05-01|2026-05-02|2026-05-03|2026-05-04|2026-05-05|2026-06-19|" \
    "2026-06-20|2026-06-21|2026-09-25|2026-09-26|2026-09-27|2026-10-01|2026-10-02|" \
    "2026-10-03|2026-10-04|2026-10-05|2026-10-06|2026-10-07|2026-10-08"
_HOLIDAY_SET = frozenset(WORKDAY_HOLIDAYS.split("|"))
# 调休上班日（法定节假日之外的周末补班）
MAKEUP_WORKDAYS = frozenset(
    {"2025-01-26", "2025-02-08", "2025-04-27", "2025-09-28", "2025-10-11",
     "2026-02-14", "2026-02-28", "2026-09-19", "2026-10-10"}
)


def _add_workdays(start: date, days: int) -> date:
    """按工作日推进（跳过周末与法定节假日，含调休补班）。"""
    current = start
    remaining = days
    while remaining > 0:
        current += timedelta(days=1)
        key = current.isoformat()
        if key in MAKEUP_WORKDAYS:
            remaining -= 1
            continue
        if current.weekday() >= 5 or key in _HOLIDAY_SET:
            continue
        remaining -= 1
    return current


def _parse_clock(text: str, offset: int = 0) -> tuple[int, int] | None:
    match = CLOCK_TIME.search(text, offset, offset + 16)
    if match is None:
        return None
    ampm, hour_raw, minute_raw = match.group(1), int(match.group(2)), match.group(3)
    minute = int(minute_raw) if minute_raw else 0
    hour = hour_raw
    if ampm in {"下午", "晚上"} and hour < 12 or ampm == "中午" and hour < 11:
        hour += 12
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


def _extract_timeline(blocks: list[Block]) -> list[dict[str, Any]]:
    timeline: list[dict[str, Any]] = []
    for block in blocks:
        text = block.text
        event = next((keyword for keyword in TIMELINE_EVENTS if keyword in text), "时间节点")
        for match in ABSOLUTE_DATE.finditer(text):
            entry: dict[str, Any] = {
                "event": event,
                "at": match.group(0),
                "source_span": _source_span(block),
                "confidence": "medium",
            }
            try:
                stamp = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
            except ValueError:
                timeline.append(entry)
                continue
            clock = _parse_clock(text, match.end())
            if clock is None:
                # 日期前面也可能写时间（「9:00 于 2025年9月19日开标」）
                tail_start = max(0, match.start() - 14)
                clock = _parse_clock(text, tail_start)
                if clock is not None and text[tail_start:match.start()].strip().startswith(
                    ("上午", "下午")
                ):
                    pass
                elif clock is not None:
                    clock = None
            if clock is not None:
                entry["deadline_at"] = datetime.combine(
                    stamp, time(clock[0], clock[1]), tzinfo=CST
                ).isoformat()
                entry["confidence"] = "high"
            timeline.append(entry)

        # 相对表述：抽不到绝对日期，但必须把「5 个工作日」原文留下来，
        # 让上层能提示「需人工确认具体日期」，而不是静默当作没有截止时间。
        # 先抹掉绝对日期占位，避免「2025年9月19日」被当成「19 日」这种相对量。
        masked = ABSOLUTE_DATE.sub(lambda m: "\u3000" * len(m.group(0)), text)
        for rel in RELATIVE_OFFSET.finditer(masked):
            quantity = int(rel.group(2))
            unit = rel.group(3)
            if quantity <= 0 or quantity > 365:
                continue
            timeline.append(
                {
                    "event": event,
                    "at": rel.group(0).strip(),
                    "relative_days": quantity,
                    "relative_unit": unit,
                    "needs_confirmation": True,
                    "source_span": _source_span(block),
                    "confidence": "low",
                }
            )
    return timeline[:100]


PUBLISH_LABELS = (
    "公告发布",
    "发布日期",
    "发布时间",
    "公告日期",
    "公告时间",
    "信息发布时间",
)


def find_publish_date(blocks: list[Block]) -> date | None:
    """从正文找「公告发布日期」，作为相对时间换算的基准。

    不能拿上传时间/项目创建时间当基准——用户可能过几天才上传，
    按上传日 +5 个工作日算出来的截止日会明显偏晚，直接误导备标。
    """
    for block in blocks:
        text = block.text
        if not any(label in text for label in PUBLISH_LABELS):
            continue
        for match in ABSOLUTE_DATE.finditer(text):
            try:
                return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
            except ValueError:
                continue
    return None


def resolve_relative_timeline(
    timeline: list[dict[str, Any]], anchor: date | None
) -> tuple[list[dict[str, Any]], list[str]]:
    """把「N 个工作日/日历日」换算成绝对日期。

    基准日取 anchor（通常为公告发布时间）。没有基准日时保持 needs_confirmation，
    绝不用「今天」去猜——猜错会让用户按错误日期准备投标。
    返回 (更新后的时间线, 提示列表)。
    """
    notes: list[str] = []
    if anchor is None:
        for entry in timeline:
            if entry.get("needs_confirmation"):
                notes.append(
                    f"「{entry['at']}」是相对表述，缺少公告发布日，无法换算具体日期，请人工确认"
                )
        return timeline, notes
    resolved: list[dict[str, Any]] = []
    for entry in timeline:
        if not entry.get("needs_confirmation"):
            resolved.append(entry)
            continue
        quantity = int(entry["relative_days"])
        unit = entry["relative_unit"]
        if unit in {"工作日"}:
            target = _add_workdays(anchor, quantity)
        elif unit in {"周"}:
            target = anchor + timedelta(weeks=quantity)
        else:
            target = anchor + timedelta(days=quantity)
        updated = dict(entry)
        updated["deadline_at"] = datetime.combine(
            target, time(17, 0), tzinfo=CST
        ).isoformat()
        updated["resolved_from"] = f"{anchor.isoformat()} + {quantity} {unit}"
        # 换算结果是推算值，时间点通常也需要人工确认（公告常不写当天几点截止）
        updated["needs_confirmation"] = True
        updated["confidence"] = "low"
        resolved.append(updated)
        notes.append(
            f"「{entry['at']}」按公告发布日 {anchor.isoformat()} 推算为 "
            f"{target.isoformat()}（默认当日 17:00 截止），请核对具体时点"
        )
    return resolved, notes


_CN_SMALL = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "壹": 1, "贰": 2, "叁": 3, "肆": 4,
    "伍": 5, "陆": 6, "柒": 7, "捌": 8, "玖": 9,
}
_CN_UNIT = {"十": 10, "拾": 10, "百": 100, "佰": 100, "千": 1000, "仟": 1000}
_CN_BIG = {"万": 10**4, "萬": 10**4, "亿": 10**8, "億": 10**8}
_CN_NUM_CHARS = "".join(_CN_SMALL) + "".join(_CN_UNIT) + "".join(_CN_BIG)


def _chinese_amount(text: str) -> Decimal | None:
    """Parse a Chinese numeral amount such as 壹佰贰拾万 or 二十万五千; None if not a clean numeral."""
    if not text:
        return None
    total = 0
    section = 0
    digit: int | None = None
    for char in text:
        if char in _CN_SMALL:
            digit = _CN_SMALL[char]
        elif char in _CN_UNIT:
            section += (digit if digit is not None else 1) * _CN_UNIT[char]
            digit = None
        elif char in _CN_BIG:
            section = (section + (digit if digit is not None else 0)) * _CN_BIG[char]
            total += section
            section = 0
            digit = None
        else:
            return None
    value = total + section + (digit if digit is not None else 0)
    return Decimal(value) if value > 0 else None


def _budget_negated(text: str, start: int, end: int) -> bool:
    """排除「预算金额未达100万元的采购项目…」这类说明性/门槛性表述。"""
    window = text[max(0, start - 8) : min(len(text), end + 8)]
    if any(token in window for token in BUDGET_NEGATION_TOKENS):
        return True
    return "不超过" in window


def _extract_budget(blocks: list[Block]) -> dict[str, Any]:
    label = (
        "预算金额|采购预算|项目预算|预算总额|最高限价|投标限价|拦标价|控制价|"
        "招标控制价|报价上限|估算价|采购估算价"
    )
    arabic = re.compile(rf"({label})[^0-9]{{0,30}}([0-9][0-9,，.]*)\s*(万元|元)")
    # 真实公告常见写法：预算金额（元）：1559100 —— 单位在数字之前
    paren_unit = re.compile(rf"({label})\s*[（(]\s*(万元|元)\s*[)）]\s*[:：]?\s*([0-9][0-9,，.]*)")
    symbol = re.compile(rf"({label})[^0-9]{{0,30}}[¥￥]\s*([0-9][0-9,，.]*)")
    uppercase = re.compile(rf"({label})[^，。;.]{{0,20}}?([{_CN_NUM_CHARS}]+)\s*(元|圆)")
    for block in blocks:
        amount: Decimal | None = None
        matched_label: str | None = None
        match = paren_unit.search(block.text)
        if match and not _budget_negated(block.text, match.start(), match.end()):
            amount = Decimal(match.group(3).replace(",", "").replace("，", ""))
            matched_label = match.group(1)
            if match.group(2) == "万元":
                amount *= 10000
        if amount is None:
            match = arabic.search(block.text)
            if match and not _budget_negated(block.text, match.start(), match.end()):
                amount = Decimal(match.group(2).replace(",", "").replace("，", ""))
                matched_label = match.group(1)
                if match.group(3) == "万元":
                    amount *= 10000
        if amount is None:
            match = symbol.search(block.text)
            if match and not _budget_negated(block.text, match.start(), match.end()):
                amount = Decimal(match.group(2).replace(",", "").replace("，", ""))
                matched_label = match.group(1)
        if amount is None:
            match = uppercase.search(block.text)
            if match and not _budget_negated(block.text, match.start(), match.end()):
                parsed = _chinese_amount(match.group(2))
                if parsed is not None:
                    amount = parsed
                    matched_label = match.group(1)
        if amount is None or amount <= 0:
            continue
        return {
            "amount": str(amount),
            "currency": "CNY",
            "label": matched_label,
            "source_span": _source_span(block),
            "confidence": "medium",
        }
    return {"amount": None, "currency": "CNY", "label": None, "source_span": None}


def _extract_checklist(blocks: list[Block]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for block in blocks:
        if not any(keyword in block.text for keyword in CHECKLIST_KEYWORDS):
            continue
        items.append(
            {
                "item": block.text[:300],
                "required": True,
                "source_span": _source_span(block),
            }
        )
    return items[:100]


def _extract_dark_bid(blocks: list[Block]) -> dict[str, Any]:
    rules: list[dict[str, Any]] = []
    for block in blocks:
        if "暗标" in block.text or "匿名" in block.text or DARK_BID_LINE.search(block.text):
            rules.append({"text": block.text[:300], "source_span": _source_span(block)})
    return {"required": bool(rules), "rules": rules[:50], "source_span": rules[0]["source_span"] if rules else None}


MANDATORY_MARKERS = ("应", "须", "必须", "不得", "不允许", "不低于", "不少于", "至少", "禁止")
REQUIREMENT_CATEGORIES = (
    ("技术", ("技术", "参数", "性能", "架构", "接口", "部署", "功能")),
    ("商务", ("报价", "付款", "合同", "质保", "违约", "发票", "税")),
    ("服务", ("服务", "响应时间", "运维", "培训", "售后", "驻场")),
    ("交付", ("工期", "交付", "验收", "进度", "到货")),
    ("安全", ("安全", "保密", "密级", "等保", "漏洞")),
)
SENTENCE_SPLIT = re.compile(r"[。；;\n]")


def _requirement_category(text: str) -> str:
    for name, keywords in REQUIREMENT_CATEGORIES:
        if any(keyword in text for keyword in keywords):
            return name
    return "其他"


def _extract_requirements(blocks: list[Block]) -> list[dict[str, Any]]:
    """Deterministic extraction of substantive (mandatory) requirements for item-by-item response."""
    requirements: list[dict[str, Any]] = []
    seen: set[str] = set()
    scoring_tables = _scoring_table_indexes(blocks)
    for block in blocks:
        if block.kind == "heading":
            continue
        if block.kind == "table_cell" and block.table_index in scoring_tables:
            continue
        if any(keyword in block.text for keyword in REJECT_KEYWORDS):
            continue
        if SCORING_LINE.match(block.text):
            continue
        for sentence in SENTENCE_SPLIT.split(block.text):
            text = sentence.strip(" \t，,、：:")
            if len(text) < 8 or len(text) > 300:
                continue
            if not any(marker in text for marker in MANDATORY_MARKERS):
                continue
            key = re.sub(r"\s+", "", text)
            if key in seen:
                continue
            seen.add(key)
            requirements.append(
                {
                    "text": text,
                    "category": _requirement_category(text),
                    "source_span": _source_span(block),
                    "confidence": "medium",
                }
            )
    return requirements[:200]


def _extract_clarifications(blocks: list[Block]) -> list[dict[str, Any]]:
    """澄清/答疑/修改通知中被实质改变的要求，纳入逐条响应以免漏答。"""
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    # 澄清文件通常只在开头声明一次“澄清/答疑”，后续条目不再重复该词
    document_declares = any(
        keyword in block.text for block in blocks for keyword in CLARIFICATION_KEYWORDS
    )
    for block in blocks:
        if block.kind == "heading":
            continue
        for sentence in SENTENCE_SPLIT.split(block.text):
            text = sentence.strip(" \t，,、：:")
            if len(text) < 10 or len(text) > 300:
                continue
            declares = any(keyword in text for keyword in CLARIFICATION_KEYWORDS)
            changes = (
                bool(CLARIFICATION_CHANGE.search(text)) or "答复" in text or "予以澄清" in text
            )
            if not (changes and (declares or document_declares)):
                continue
            key = re.sub(r"\s+", "", text)
            if key in seen:
                continue
            seen.add(key)
            items.append(
                {
                    "text": text,
                    "source_span": _source_span(block),
                    "confidence": "medium",
                }
            )
    return items[:100]


def build_constraint_set(parsed: ParsedDocument, project_meta: dict[str, Any]) -> dict[str, Any]:
    return {
        "project": project_meta,
        "reject_clauses": _extract_reject_clauses(parsed.blocks),
        "scoring_items": _extract_scoring(parsed.blocks),
        "qualification_requirements": _extract_qualifications(parsed.blocks),
        "substantive_requirements": _extract_requirements(parsed.blocks),
        "clarifications": _extract_clarifications(parsed.blocks),
        "packages": extract_packages(parsed.blocks, _source_span),
        "format_rules": _extract_format_rules(parsed.blocks),
        "timeline": _extract_timeline(parsed.blocks),
        "budget": _extract_budget(parsed.blocks),
        "bid_validity_days": _extract_bid_validity_days(parsed.blocks),
        "deposit": _extract_deposit(parsed.blocks),
        "front_appendix": _extract_front_appendix(parsed.blocks),
        "star_clauses": _extract_star_clauses(parsed.blocks),
        "technical_parameters": _extract_technical_parameters(parsed.blocks),
        "submission_checklist": _extract_checklist(parsed.blocks),
        "dark_bid": _extract_dark_bid(parsed.blocks),
        "bid_risks": extract_bid_risks(parsed.blocks),
        "parse_meta": {
            "parser_version": PARSER_VERSION,
            "ocr_used": False,
            "llm_used": False,
            "warnings": parsed.warnings,
        },
    }


def parsed_bid_deadline(timeline: list[dict[str, Any]]) -> datetime | None:
    """Only an explicit 投标截止/递交 date with a clock time; never inferred from publish date."""
    for entry in timeline:
        if entry.get("event") in DEADLINE_EVENTS and entry.get("deadline_at"):
            try:
                return datetime.fromisoformat(str(entry["deadline_at"]))
            except ValueError:
                continue
    return None
