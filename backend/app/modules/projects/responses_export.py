from datetime import UTC, datetime
from io import BytesIO
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from app.core.spreadsheet import active_sheet, excel_row
from app.modules.projects.export_branding import ExportBranding, append_branding_row

STATUS_LABELS = {
    "responded": "已响应",
    "deviated": "偏离",
    "not_responded": "不响应",
    "pending": "待确认",
}
TYPE_LABELS = {
    "qualification": "资格要求",
    "substantive": "实质性要求",
    "scoring": "评分项",
    "clarification": "澄清事项",
}


def _format_source(span: Any) -> str:
    """把解析时留下的定位信息拼成人工可翻回招标文件的出处。"""
    if not isinstance(span, dict):
        return ""
    parts: list[str] = []
    if span.get("page"):
        parts.append(f"第 {span['page']} 页")
    if span.get("table_index") is not None:
        parts.append(f"表 {span['table_index']}")
    if span.get("row") is not None:
        parts.append(f"行 {span['row']}")
    if span.get("col") is not None:
        parts.append(f"列 {span['col']}")
    if not parts and span.get("block_index") is not None:
        parts.append(f"块 {span['block_index']}")
    quote = str(span.get("quote") or "").strip()
    if quote:
        parts.append(quote[:60])
    return " · ".join(parts)


def build_responses_xlsx(
    project_name: str,
    items: list[dict[str, Any]],
    *,
    branding: ExportBranding | None = None,
) -> bytes:
    workbook = Workbook()
    summary = active_sheet(workbook)
    summary.title = "Summary"
    summary["A1"] = "BidCopilot 逐条响应 / 偏离表"
    summary["A1"].font = Font(size=14, bold=True)
    # 品牌行紧随标题；branding=None 时不写任何内容，既有布局与断言不变。
    append_branding_row(summary, branding)
    counts: dict[str, int] = {status: 0 for status in STATUS_LABELS}
    for item in items:
        status = str(item.get("response_status"))
        counts[status] = counts.get(status, 0) + 1
    summary.append(excel_row(["项目", project_name]))
    summary.append(["导出时间", datetime.now(UTC).isoformat(timespec="seconds")])
    summary.append(["条目总数", len(items)])
    for status, label in STATUS_LABELS.items():
        summary.append([label, counts.get(status, 0)])
    summary.append(["未处理合计", len(items) - counts.get("responded", 0)])
    summary.column_dimensions["A"].width = 18
    summary.column_dimensions["B"].width = 60

    sheet = workbook.create_sheet("Responses")
    headers = [
        "序号",
        "类型",
        "类别",
        "招标要求原文",
        "招标出处",
        "响应状态",
        "响应说明",
        "对应章节",
        "证据引用",
        "确认时间",
    ]
    sheet.append(headers)
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="334155")

    for index, item in enumerate(items, start=1):
        sheet.append(
            excel_row(
                [
                    index,
                    TYPE_LABELS.get(
                        str(item.get("requirement_type")), str(item.get("requirement_type"))
                    ),
                    item.get("category") or "",
                    item.get("requirement_text") or "",
                    _format_source(item.get("source_span")),
                    STATUS_LABELS.get(
                        str(item.get("response_status")), str(item.get("response_status"))
                    ),
                    item.get("response_note") or "",
                    item.get("section_ref") or "",
                    "、".join(str(ref) for ref in item.get("evidence_refs") or []),
                    item.get("confirmed_at") or "",
                ]
            )
        )

    widths = [6, 12, 10, 60, 40, 10, 30, 14, 24, 22]
    for position, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(position)].width = width
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    sheet.freeze_panes = "A2"

    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
