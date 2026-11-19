from io import BytesIO

from docx import Document
from docx.shared import Pt

from app.modules.generation.formatting import apply_format

PROFILE = {
    "page": {"size": "A4", "margins_cm": {"top": 2.0, "bottom": 2.0, "left": 2.6, "right": 2.6}},
    "body": {"font": "宋体", "size_pt": 11, "line_spacing": 1.35, "first_line_indent_chars": 2},
    "headings": [
        {"level": 1, "font": "黑体", "size_pt": 15, "bold": True},
        {"level": 2, "font": "黑体", "size_pt": 13, "bold": True},
    ],
}


def test_apply_format_sets_page_styles_and_page_number() -> None:
    document = Document()
    document.add_heading("技术方案", level=1)
    document.add_heading("实施计划", level=2)
    document.add_paragraph("正文内容")

    apply_format(document, PROFILE)

    section = document.sections[0]
    assert round(section.top_margin.cm, 1) == 2.0
    assert round(section.left_margin.cm, 1) == 2.6
    assert round(section.page_width.cm, 1) == 21.0
    normal = document.styles["Normal"]
    assert normal.font.name == "宋体"
    assert normal.font.size == Pt(11)
    assert normal.paragraph_format.line_spacing == 1.35
    assert document.styles["Heading 1"].font.name == "黑体"
    assert document.styles["Heading 1"].font.size == Pt(15)

    buffer = BytesIO()
    document.save(buffer)
    reloaded = Document(BytesIO(buffer.getvalue()))
    footer_xml = reloaded.sections[0].footer._element.xml
    assert "PAGE" in footer_xml
