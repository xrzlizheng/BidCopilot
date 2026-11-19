from typing import Any

from openpyxl import Workbook
from openpyxl.worksheet.worksheet import Worksheet


def active_sheet(workbook: Workbook) -> Worksheet:
    """取 Workbook 的活动工作表，并把 openpyxl-stubs 标注的 Optional 收窄。

    `Workbook()` 构造后必有一张活动表；stubs 仍按「可能是 Chartsheet」标了
    `| None`，这里显式判空：既满足静态检查，也保留运行时保护。
    """
    sheet = workbook.active
    if sheet is None:  # pragma: no cover - 构造后必有活动表
        raise RuntimeError("workbook has no active sheet")
    return sheet


def excel_safe(value: Any) -> Any:
    if not isinstance(value, str) or not value:
        return value
    if value.startswith("'"):
        return value
    if value.lstrip()[:1] in {"=", "+", "-", "@"} or value[:1] in {"\t", "\r"}:
        return "'" + value
    return value


def excel_row(values: list[Any]) -> list[Any]:
    return [excel_safe(value) for value in values]
