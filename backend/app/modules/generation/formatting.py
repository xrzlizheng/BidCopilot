from typing import Any

from docx.document import Document as DocxDocument
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt

PAGE_SIZES_CM = {"A4": (21.0, 29.7), "LETTER": (21.59, 27.94)}


def _set_east_asia(run_or_style: Any, font_name: str) -> None:
    element = (
        run_or_style._element if hasattr(run_or_style, "_element") else run_or_style.element
    )
    rpr = element.get_or_add_rPr()
    fonts = rpr.find(qn("w:rFonts"))
    if fonts is None:
        fonts = OxmlElement("w:rFonts")
        rpr.append(fonts)
    fonts.set(qn("w:eastAsia"), font_name)


def _add_page_number(document: DocxDocument) -> None:
    footer = document.sections[0].footer
    paragraph = footer.paragraphs[0] if footer.paragraphs else footer.add_paragraph()
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = paragraph.add_run()
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instruction = OxmlElement("w:instrText")
    instruction.set(qn("xml:space"), "preserve")
    instruction.text = "PAGE"
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    run._r.append(begin)
    run._r.append(instruction)
    run._r.append(end)


def apply_format(document: DocxDocument, profile: dict[str, Any]) -> None:
    page = profile.get("page") or {}
    width_height = PAGE_SIZES_CM.get(str(page.get("size") or "A4").upper())
    margins = page.get("margins_cm") or {}
    for section in document.sections:
        if width_height:
            section.page_width = Cm(width_height[0])
            section.page_height = Cm(width_height[1])
        if margins:
            section.top_margin = Cm(float(margins.get("top", 2.54)))
            section.bottom_margin = Cm(float(margins.get("bottom", 2.54)))
            section.left_margin = Cm(float(margins.get("left", 3.18)))
            section.right_margin = Cm(float(margins.get("right", 3.18)))

    body = profile.get("body") or {}
    if body:
        style = document.styles["Normal"]
        font_name = str(body.get("font") or "宋体")
        style.font.name = font_name
        _set_east_asia(style, font_name)
        style.font.size = Pt(float(body.get("size_pt", 12)))
        paragraph_format = style.paragraph_format
        paragraph_format.line_spacing = float(body.get("line_spacing", 1.5))
        indent_chars = int(body.get("first_line_indent_chars", 2))
        paragraph_format.first_line_indent = Pt(float(body.get("size_pt", 12)) * indent_chars)

    for heading in profile.get("headings") or []:
        level = int(heading.get("level", 1))
        style_name = f"Heading {level}"
        if style_name not in document.styles:
            continue
        style = document.styles[style_name]
        font_name = str(heading.get("font") or "黑体")
        style.font.name = font_name
        _set_east_asia(style, font_name)
        style.font.size = Pt(float(heading.get("size_pt", 16)))
        style.font.bold = bool(heading.get("bold", True))

    _add_page_number(document)
