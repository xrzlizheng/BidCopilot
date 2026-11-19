"""文字层 PDF 表格解析（pdfplumber 主路径 + pypdf 回退）的形状锁定。"""

from io import BytesIO

import pytest

from app.modules.tender.parser import (
    ParsedDocument,
    ScannedDocumentError,
    _parse_pdf,
    parse_document,
)
from tests.gold_corpus import _pdf_par, _pdf_style, _pdf_table_bytes


def _pdf_with_table(extra_paragraphs=()):
    from reportlab.platypus import Table

    table = Table(
        [
            ["评分项", "分值"],
            ["技术方案", "30"],
            ["投标报价", "20"],
        ],
        colWidths=[240, 90],
    )
    table.setStyle(_pdf_style())
    return _pdf_table_bytes(
        [table, *[_pdf_par(text) for text in extra_paragraphs]]
    )


def test_pdf_table_produces_table_cell_blocks_with_grid_shape() -> None:
    parsed = _parse_pdf(_pdf_with_table())

    cells = [block for block in parsed.blocks if block.kind == "table_cell"]
    assert cells
    header = next(block for block in cells if block.text == "评分项")
    assert header.table_index == 0 and header.row == 0 and header.col == 0
    assert header.header is True and header.page == 1
    value = next(block for block in cells if block.text == "30")
    assert value.row == 1 and value.col == 1 and value.header is False


def test_pdf_paragraphs_outside_table_are_kept_and_not_duplicated() -> None:
    content = _pdf_with_table(extra_paragraphs=["预算金额：人民币 100 万元。"])
    parsed = _parse_pdf(content)

    assert any(
        block.kind == "paragraph" and "预算金额" in block.text for block in parsed.blocks
    )
    # 表格内容不应同时以段落块重复出现
    assert not any(block.kind == "paragraph" and block.text == "评分项" for block in parsed.blocks)


def test_two_tables_increment_table_index() -> None:
    from reportlab.platypus import Table

    first = Table([["评分项", "分值"], ["方案", "10"]], colWidths=[200, 80])
    first.setStyle(_pdf_style())
    second = Table([["项目", "金额（元）"], ["租用", "1200"]], colWidths=[200, 80])
    second.setStyle(_pdf_style())
    parsed = _parse_pdf(_pdf_table_bytes([first, _pdf_par("报价表："), second]))

    indexes = {block.table_index for block in parsed.blocks if block.kind == "table_cell"}
    assert indexes == {0, 1}


def test_pdfplumber_failure_falls_back_to_pypdf_with_warning(monkeypatch) -> None:
    content = _pdf_with_table()

    class _Boom:
        def __enter__(self):
            raise RuntimeError("corrupt xref")

        def __exit__(self, *args):
            return False

    import sys

    monkeypatch.setitem(sys.modules, "pdfplumber", None)
    # pdfplumber.open 触发异常：注入伪模块使 open 抛错
    import types

    fake = types.ModuleType("pdfplumber")
    fake.open = lambda *_args, **_kwargs: _Boom()
    monkeypatch.setitem(sys.modules, "pdfplumber", fake)

    parsed = _parse_pdf(content)

    assert parsed.blocks  # pypdf 兜底出纯文本块
    assert any(w.startswith("pdf_fallback:") for w in parsed.warnings)


def test_scanned_pdf_without_text_layer_still_requires_ocr() -> None:
    from reportlab.pdfgen import canvas

    buffer = BytesIO()
    page = canvas.Canvas(buffer)
    page.showPage()  # 空白页：无文字层
    page.save()

    with pytest.raises(ScannedDocumentError):
        _parse_pdf(buffer.getvalue())


def test_parse_document_dispatch_and_version() -> None:
    parsed = parse_document(_pdf_with_table(), "tender.pdf")
    assert isinstance(parsed, ParsedDocument)
    assert parsed.page_count == 1
