import re
from decimal import Decimal, InvalidOperation
from typing import Any

from app.modules.compliance.registry import Rule
from app.modules.tender.parser import _chinese_amount

CONSISTENCY_METRICS = {
    "工期": "days",
    "服务期": "days",
    "质保期": "days",
    "保修期": "days",
    "维保期": "days",
    "交付周期": "days",
    "可用性": "percent",
    "响应时间": "minutes",
}

CONSISTENCY_UNITS = {
    "天": ("days", 1.0),
    "日": ("days", 1.0),
    "个月": ("days", 30.0),
    "月": ("days", 30.0),
    "年": ("days", 365.0),
    "小时": ("minutes", 60.0),
    "分钟": ("minutes", 1.0),
    "%": ("percent", 1.0),
}

CONSISTENCY_PATTERN = re.compile(
    r"(工期|服务期|质保期|保修期|维保期|交付周期|可用性|响应时间)"
    r"[^0-9\n]{0,12}?([0-9]+(?:\.[0-9]+)?)\s*(个月|月|天|日|年|小时|分钟|%)"
)


def check_consistency_numbers(rule: Rule, context: Any, finding: Any) -> list[dict[str, Any]]:
    """同一指标在全文出现多个不同取值时报告，单位按天/分钟/百分比归一后比较。"""
    text = context.content_text or ""
    found: dict[str, dict[float, list[str]]] = {}
    for match in CONSISTENCY_PATTERN.finditer(text):
        concept, raw, unit = match.group(1), match.group(2), match.group(3)
        conversion = CONSISTENCY_UNITS.get(unit)
        if conversion is None:
            continue
        family, factor = conversion
        if family != CONSISTENCY_METRICS.get(concept):
            continue
        value = round(float(raw) * factor, 4)
        found.setdefault(concept, {}).setdefault(value, []).append(match.group(0).strip())

    results: list[dict[str, Any]] = []
    for concept, values in found.items():
        if len(values) < 2:
            continue
        samples = [sorted(group)[0] for _, group in sorted(values.items())]
        results.append(
            finding(
                rule,
                message=f"{concept}在全文出现多个不同取值",
                location={"scope": "content"},
                actual="；".join(samples),
                suggestion="统一为一处口径，并确认较低取值不构成负偏离。",
            )
        )
    return results[:10]


QUOTE_LABELS = (
    "投标总价",
    "投标总报价",
    "投标报价",
    "报价总额",
    "开标一览表",
    "合计金额",
    "总价",
)
QUOTE_AMOUNT = re.compile(
    r"(投标总价|投标总报价|投标报价|报价总额|开标一览表|合计金额|总价)"
    r"[^0-9\n]{0,20}?([0-9][0-9,，.]*)\s*(万元|元|¥|￥)"
)
UPPER_AMOUNT = re.compile(
    r"(?:大写|人民币大写|金额大写)[^。；\n]{0,10}?[：: ]?\s*"
    r"([壹贰叁肆伍陆柒捌玖拾佰仟万亿零〇圆元角分]{2,})"
)
NUMERIC_AMOUNT = re.compile(r"(?:小写|¥|￥|人民币)?\s*([0-9][0-9,，.]*)\s*(万元|元)?")


def _decimal_amount(raw: str, unit: str | None = None) -> Decimal | None:
    try:
        value = Decimal(raw.replace(",", "").replace("，", ""))
    except (InvalidOperation, ValueError):
        return None
    if unit == "万元":
        value *= 10000
    return value


def _cell_amount(text: str) -> Decimal | None:
    match = re.search(r"([0-9][0-9,，.]*)\s*(万元|元)?", text or "")
    if match is None:
        return None
    return _decimal_amount(match.group(1), match.group(2))


def _column_index(header: list[str], aliases: tuple[str, ...]) -> int | None:
    for index, text in enumerate(header):
        if any(alias in text for alias in aliases):
            return index
    return None


def _docx_quote_rows(document: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for table in getattr(document, "tables", []) or []:
        if not getattr(table, "rows", None):
            continue
        header = [cell.text.strip() for cell in table.rows[0].cells]

        unit_col = _column_index(header, ("单价", "单位价格"))
        quantity_col = _column_index(header, ("数量", "工程量", "数目"))
        total_col = _column_index(header, ("合价", "金额", "总价", "报价"))
        if unit_col is None or quantity_col is None or total_col is None:
            continue
        for row in table.rows[1:]:
            cells = [cell.text.strip() for cell in row.cells]
            if max(unit_col, quantity_col, total_col) >= len(cells):
                continue
            unit = _cell_amount(cells[unit_col])
            quantity = _cell_amount(cells[quantity_col])
            total = _cell_amount(cells[total_col])
            if unit is None or quantity is None or total is None:
                continue
            rows.append(
                {
                    "unit": unit,
                    "quantity": quantity,
                    "total": total,
                    "text": " | ".join(cells)[:300],
                }
            )
    return rows


def check_quote_arithmetic(rule: Rule, context: Any, finding: Any) -> list[dict[str, Any]]:
    """报价总表/分表/一览表、大小写、单价×数量的确定性核对；无结构则不猜。"""
    results: list[dict[str, Any]] = []
    labeled: list[tuple[str, Decimal]] = []
    for match in QUOTE_AMOUNT.finditer(context.content_text or ""):
        value = _decimal_amount(match.group(2), match.group(3))
        if value is not None and value > 0:
            labeled.append((match.group(1), value))
    distinct = sorted({str(value) for _, value in labeled})
    if len(distinct) > 1:
        samples = "；".join(f"{label} {value}" for label, value in labeled[:6])
        results.append(
            finding(
                rule,
                message="报价总价/分表/开标一览表出现多个不同金额",
                location={"scope": "content"},
                expected="同一报价在各表口径一致",
                actual=samples,
                suggestion="核对投标函、开标一览表、报价明细表与分项合价。",
            )
        )

    for line in (context.content_text or "").splitlines():
        upper = UPPER_AMOUNT.search(line)
        if upper is None:
            continue
        chinese = _chinese_amount(
            upper.group(1)
            .replace("圆", "")
            .replace("元", "")
            .replace("整", "")
            .replace("正", "")
        )
        numeric = NUMERIC_AMOUNT.search(line)
        if chinese is None or numeric is None:
            continue
        value = _decimal_amount(numeric.group(1), numeric.group(2))
        if value is None:
            continue
        if abs(chinese - value) > Decimal("0.01"):
            results.append(
                finding(
                    rule,
                    message="报价大写与小写金额不一致",
                    location={"scope": "content"},
                    expected=str(value),
                    actual=str(chinese),
                    suggestion="以评审要求为准统一大写与小写金额。",
                )
            )
            if len(results) >= 10:
                break

    for row in _docx_quote_rows(getattr(context, "docx_document", None)):
        if abs(row["unit"] * row["quantity"] - row["total"]) > Decimal("0.01"):
            results.append(
                finding(
                    rule,
                    message="报价明细的单价×数量与合价不一致",
                    location={"scope": "docx_table"},
                    expected=str(row["unit"] * row["quantity"]),
                    actual=str(row["total"]),
                    suggestion="逐行重算工程量清单/报价明细，确认无漏项、无算术错误。",
                )
            )
            if len(results) >= 10:
                break
    return results[:10]


def check_deposit_limit(rule: Rule, context: Any, finding: Any) -> list[dict[str, Any]]:
    """保证金金额不超过估算价 2%、有效期不短于投标有效期；缺数据不猜。"""
    payload = context.payload or {}
    deposit = payload.get("deposit") or {}
    budget = payload.get("budget") or {}
    label = str(budget.get("label") or "")
    ceiling_labels = {"最高限价", "控制价", "招标控制价", "估算价", "采购估算价"}
    results: list[dict[str, Any]] = []
    try:
        deposit_amount = (
            Decimal(str(deposit.get("amount"))) if deposit.get("amount") else None
        )
        ceiling = Decimal(str(budget.get("amount"))) if budget.get("amount") else None
    except (InvalidOperation, ValueError):
        deposit_amount = ceiling = None
    if deposit_amount is not None and ceiling is not None and label in ceiling_labels:
        limit = ceiling * Decimal("0.02")
        if deposit_amount > limit:
            results.append(
                finding(
                    rule,
                    message="投标保证金金额超过估算价的 2%",
                    location=deposit.get("source_span") or {"scope": "deposit"},
                    expected=f"≤ {limit}",
                    actual=str(deposit_amount),
                    suggestion="核对招标文件保证金条款；超过法定上限可要求澄清/质疑。",
                )
            )
    validity_days = deposit.get("validity_days")
    bid_validity_days = payload.get("bid_validity_days")
    if (
        isinstance(validity_days, int)
        and isinstance(bid_validity_days, int)
        and validity_days > 0
        and bid_validity_days > 0
        and validity_days < bid_validity_days
    ):
        results.append(
            finding(
                rule,
                message="投标保证金有效期短于投标有效期",
                location=deposit.get("source_span") or {"scope": "deposit"},
                expected=f"≥ {bid_validity_days} 天",
                actual=f"{validity_days} 天",
                suggestion="按招标文件要求延长保证金有效期，避免被否决。",
            )
        )
    return results[:10]


def check_package_selection(rule: Rule, context: Any, finding: Any) -> list[dict[str, Any]]:
    """多标包招标必须指明本次投标的包件。"""
    packages = (context.payload or {}).get("packages") or {}
    declared = packages.get("declared_count")
    items = packages.get("items") or []
    if not (declared and declared > 1) and len(items) < 2:
        return []
    if getattr(context.project, "package_code", None):
        return []
    label = "、".join(f"{item.get('code')} {item.get('name')}".strip() for item in items[:5])
    return [
        finding(
            rule,
            message="招标文件划分多个标包，但项目未指定所投包件",
            location={"scope": "project"},
            actual=label or f"声明 {declared} 个标包",
            suggestion="在项目设置中选择本次投标的包件后再生成与导出。",
        )
    ]


OPEN_STATUSES = {"pending", "deviated", "not_responded"}
STATUS_TEXT = {"pending": "待确认", "deviated": "偏离", "not_responded": "不响应"}


def check_unlinked_responses(rule: Rule, context: Any, finding: Any) -> list[dict[str, Any]]:
    """已响应却定位不到章节：清标时无人能核实，状态等于自说自话。"""
    responses = getattr(context, "responses", None) or []
    unlinked = [
        str(getattr(row, "requirement_text", ""))[:40]
        for row in responses
        if getattr(row, "response_status", None) == "responded"
        and not (getattr(row, "section_ref", None) or "").strip()
    ]
    if not unlinked:
        return []
    return [
        finding(
            rule,
            message="已响应条目未指向具体章节",
            location={"scope": "responses"},
            actual=f"{len(unlinked)} 条；示例：{'；'.join(unlinked[:5])}",
            suggestion="在逐条响应表里为每条已响应要求关联对应章节，便于评审核对出处。",
        )
    ]


def check_open_responses(rule: Rule, context: Any, finding: Any) -> list[dict[str, Any]]:
    """逐条响应表中仍有未处理条目时提示：漏响应是废标的直接原因。"""
    responses = getattr(context, "responses", None) or []
    if not responses:
        return []
    counts: dict[str, int] = {}
    samples: list[str] = []
    for row in responses:
        status = getattr(row, "response_status", None)
        if status not in OPEN_STATUSES:
            continue
        counts[status] = counts.get(status, 0) + 1
        if len(samples) < 5:
            samples.append(str(getattr(row, "requirement_text", ""))[:40])
    if not counts:
        return []
    detail = "、".join(f"{STATUS_TEXT[status]}{count}" for status, count in counts.items())
    return [
        finding(
            rule,
            message="逐条响应表仍有未处理条目",
            location={"scope": "responses"},
            actual=f"{detail}；示例：{'；'.join(samples)}",
            suggestion="逐条标记为已响应或说明偏离原因后再提交。",
        )
    ]
