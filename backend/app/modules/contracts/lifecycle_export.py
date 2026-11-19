"""合同履约台账 xlsx 导出：表头 + 合计行，所有文本经 excel_row 公式注入防护。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from io import BytesIO
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from app.core.spreadsheet import active_sheet, excel_row

LEDGER_TYPE_LABELS = {
    "invoice": "开票",
    "payment": "收款",
    "deposit": "保证金",
    "retention": "质保金",
}


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def build_ledger_xlsx(
    *,
    contract_no: str,
    title: str,
    currency: str,
    entries: list[dict[str, Any]],
    summary: dict[str, Decimal],
    disclaimer: str,
) -> bytes:
    workbook = Workbook()
    overview = active_sheet(workbook)
    overview.title = "合同概要"
    overview["A1"] = "BidCopilot 合同履约台账"
    overview["A1"].font = Font(size=14, bold=True)
    overview.append(excel_row(["合同编号", contract_no]))
    overview.append(excel_row(["合同名称", title]))
    overview.append(excel_row(["币种", currency]))
    overview.append(excel_row(["已开票合计", summary["invoice_total"]]))
    overview.append(excel_row(["已收款合计", summary["payment_total"]]))
    overview.append(excel_row(["未收余额", summary["outstanding"]]))
    overview.append(excel_row(["声明", disclaimer]))
    overview.column_dimensions["A"].width = 18
    overview.column_dimensions["B"].width = 80

    sheet = workbook.create_sheet("履约台账")
    headers = ["序号", "类型", "金额", "发生日", "备注", "凭证引用", "创建时间"]
    sheet.append(headers)
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="334155")
        cell.alignment = Alignment(vertical="center")

    row_sum = Decimal("0.00")
    for index, entry in enumerate(entries, start=1):
        amount = entry["amount"]
        if isinstance(amount, float):
            raise TypeError("台账金额必须使用 Decimal，禁止 float")
        row_sum += amount if isinstance(amount, Decimal) else Decimal(str(amount))
        sheet.append(
            excel_row(
                [
                    index,
                    LEDGER_TYPE_LABELS.get(str(entry["entry_type"]), str(entry["entry_type"])),
                    amount,
                    _text(entry.get("occurred_on")),
                    _text(entry.get("note")),
                    _text(entry.get("voucher_ref")),
                    _text(entry.get("created_at")),
                ]
            )
        )

    summary_start = sheet.max_row + 2
    sheet.append(excel_row(["合计", "", row_sum.quantize(Decimal("0.01")), "", "", "", ""]))
    sheet.append(excel_row(["已开票合计", "", summary["invoice_total"], "", "", "", ""]))
    sheet.append(excel_row(["已收款合计", "", summary["payment_total"], "", "", "", ""]))
    sheet.append(excel_row(["未收余额", "", summary["outstanding"], "", "", "", ""]))
    for row in sheet.iter_rows(min_row=summary_start, max_row=sheet.max_row):
        row[0].font = Font(bold=True)
    for index, width in enumerate([8, 12, 16, 14, 40, 28, 22], start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    sheet.freeze_panes = "A2"

    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
