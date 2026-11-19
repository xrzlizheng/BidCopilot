from io import BytesIO

from openpyxl import load_workbook

from app.core.spreadsheet import excel_safe
from app.modules.projects.responses_export import build_responses_xlsx


def test_excel_safe_prefixes_formula_like_values() -> None:
    assert excel_safe("=HYPERLINK(\"http://evil\")") == "'=HYPERLINK(\"http://evil\")"
    assert excel_safe(" +cmd") == "' +cmd"
    assert excel_safe("-1") == "'-1"
    assert excel_safe("@cmd") == "'@cmd"
    assert excel_safe("normal") == "normal"
    assert excel_safe(3) == 3


def test_responses_export_does_not_create_formulas() -> None:
    payload = build_responses_xlsx(
        "=cmd|' /C calc'!A0",
        [
            {
                "requirement_type": "=1+1",
                "category": "@SUM(1)",
                "requirement_text": "=HYPERLINK(\"http://evil\")",
                "response_status": "pending",
            }
        ],
    )
    workbook = load_workbook(BytesIO(payload))
    assert workbook["Summary"]["B2"].data_type == "s"
    assert workbook["Responses"]["D2"].data_type == "s"
    assert workbook["Responses"]["D2"].value.startswith("'=")
    workbook.close()
