from datetime import UTC, datetime
from io import BytesIO
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from app.core.spreadsheet import active_sheet, excel_row
from app.modules.projects.export_branding import ExportBranding, append_branding_row

STATUS_LABELS = {
    "pending": "待处理",
    "done": "已完成",
    "blocked": "阻断",
    "not_applicable": "不适用",
    "waived": "已豁免",
}
SOURCE_LABELS = {"auto": "自动检查", "manual": "人工确认"}


def build_readiness_xlsx(
    project_name: str,
    payload: dict[str, Any],
    *,
    branding: ExportBranding | None = None,
) -> bytes:
    workbook = Workbook()
    summary = active_sheet(workbook)
    summary.title = "Summary"
    summary["A1"] = "BidCopilot 交标就绪清单"
    summary["A1"].font = Font(size=14, bold=True)
    # 品牌行紧随标题；branding=None 时不写任何内容，既有布局与断言不变。
    append_branding_row(summary, branding)
    template = payload.get("template") or {}
    counts = payload.get("summary") or {}
    summary.append(excel_row(["项目", project_name]))
    summary.append(excel_row(["模板", f"{template.get('name', '')} v{template.get('version', '')}"]))
    summary.append(excel_row(["导出时间", datetime.now(UTC).isoformat(timespec="seconds")]))
    summary.append(excel_row(["投标截止", str(payload.get("bid_deadline") or "未解析")]))
    summary.append(excel_row(["内部截止", str(payload.get("internal_deadline") or "未设置")]))
    for key in ("total", "done", "blocked", "pending", "not_applicable", "waived", "blocking_open"):
        summary.append(excel_row([STATUS_LABELS.get(key, key), counts.get(key, 0)]))
    summary.append(excel_row(["完成率", counts.get("completion_rate", 0)]))
    summary.column_dimensions["A"].width = 18
    summary.column_dimensions["B"].width = 70

    sheet = workbook.create_sheet("Readiness")
    headers = [
        "序号",
        "阶段",
        "类别",
        "检查项",
        "来源",
        "阻断",
        "状态",
        "责任人",
        "协作人",
        "截止",
        "证据",
        "备注",
        "豁免/不适用理由",
        "检查时间",
    ]
    sheet.append(headers)
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="334155")

    for index, item in enumerate(payload.get("items") or [], start=1):
        sheet.append(
            excel_row(
                [
                    index,
                    item.get("stage") or "",
                    item.get("category") or "",
                    item.get("label") or "",
                    SOURCE_LABELS.get(str(item.get("source_type")), str(item.get("source_type"))),
                    "是" if item.get("is_blocking") else "否",
                    STATUS_LABELS.get(str(item.get("status")), str(item.get("status"))),
                    item.get("owner_id") or "",
                    "、".join(item.get("collaborator_ids") or []),
                    item.get("due_at") or "",
                    "、".join(item.get("evidence_refs") or []),
                    item.get("note") or "",
                    item.get("waived_reason") or "",
                    item.get("synced_at") or "",
                ]
            )
        )

    widths = [6, 10, 16, 46, 12, 8, 10, 22, 22, 22, 26, 30, 30, 22]
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    sheet.freeze_panes = "A2"

    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
