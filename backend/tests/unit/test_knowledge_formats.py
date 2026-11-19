from io import BytesIO

from docx import Document
from openpyxl import Workbook
from pptx import Presentation

from app.modules.knowledge.kb import extract_document_text


def test_extract_xlsx_sheets_and_rows() -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "产品清单"
    sheet.append(["产品", "说明"])
    sheet.append(["星云运维平台", "支持双活架构"])
    buffer = BytesIO()
    workbook.save(buffer)

    text = extract_document_text("products.xlsx", ".xlsx", buffer.getvalue())

    assert "工作表：产品清单" in text
    assert "星云运维平台" in text
    assert "支持双活架构" in text


def test_extract_docx_table_rows_keeps_cell_relationships() -> None:
    document = Document()
    document.add_paragraph("产品参数表")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "产品"
    table.cell(0, 1).text = "参数"
    table.cell(1, 0).text = "星云运维平台"
    table.cell(1, 1).text = "双活架构"
    buffer = BytesIO()
    document.save(buffer)

    text = extract_document_text("params.docx", ".docx", buffer.getvalue())

    assert "产品 | 参数" in text
    assert "星云运维平台 | 双活架构" in text


def test_extract_pptx_slide_text_and_table() -> None:
    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[1])
    slide.shapes.title.text = "星云运维平台方案"
    slide.placeholders[1].text = "双活架构与自动化巡检"
    buffer = BytesIO()
    presentation.save(buffer)

    text = extract_document_text("deck.pptx", ".pptx", buffer.getvalue())

    assert "幻灯片 1" in text
    assert "星云运维平台方案" in text
    assert "双活架构与自动化巡检" in text
