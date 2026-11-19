from io import BytesIO

from docx import Document as DocxDocument

from app.modules.compliance.engine import _check_format_assembly
from app.modules.compliance.registry import load_rule_set
from app.modules.tender.assembly import analyze_final_document


def _rule():
    _, rules = load_rule_set()
    return next(rule for rule in rules if rule.rule_id == "FORMAT-ASSEMBLY-001")


def _docx_bytes(*, cover_name: str = "", toc: bool = False, page_footer: bool = False) -> bytes:
    document = DocxDocument()
    if cover_name:
        document.add_paragraph(cover_name)
    if toc:
        document.add_paragraph("目录")
        document.add_paragraph("第一章 项目理解")
    if page_footer:
        footer = document.sections[0].footer
        footer.paragraphs[0].text = "PAGE"
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def test_final_document_assembly_analyzer() -> None:
    good = analyze_final_document(
        _docx_bytes(cover_name="政务云项目", toc=True, page_footer=True),
        "final.docx",
        project_name="政务云项目",
    )
    assert good["analyzed"] is True
    assert good["has_cover_name"] is True
    assert good["has_toc"] is True
    assert good["has_page_numbers"] is True

    missing = analyze_final_document(
        _docx_bytes(), "final.docx", project_name="政务云项目"
    )
    assert missing["has_cover_name"] is False
    assert missing["has_toc"] is False
    assert missing["has_page_numbers"] is False


class _Context:
    def __init__(self, final_assembly: dict) -> None:
        self.payload = {"format_rules": [{"expect": {"text": "投标文件应有封面、目录和页码"}}]}
        self.final_assembly = final_assembly

        class Project:
            name = "政务云项目"

        self.project = Project()
        self.docx_document = DocxDocument()
        self.docx_text = ""
        self.content_text = ""


def test_format_assembly_rule_prefers_final_document() -> None:
    context = _Context(
        {"analyzed": True, "format": "docx", "has_cover_name": False, "has_toc": False, "has_page_numbers": False}
    )
    findings = _check_format_assembly(_rule(), context)
    messages = " ".join(item["message"] for item in findings)
    assert "最终标书封面" in messages
    assert "最终标书未检测到目录" in messages
    assert "最终标书页脚" in messages

    clean = _Context(
        {"analyzed": True, "format": "pdf", "has_cover_name": True, "has_toc": True, "has_page_numbers": None}
    )
    assert _check_format_assembly(_rule(), clean) == []


def test_final_document_assembly_header_page_numbers() -> None:
    document = DocxDocument()
    header = document.sections[0].header
    header.paragraphs[0].text = "PAGE"
    buffer = BytesIO()
    document.save(buffer)
    res = analyze_final_document(buffer.getvalue(), "final.docx", project_name="")
    assert res["has_page_numbers"] is True


def test_final_document_corrupted_content_handled_gracefully() -> None:
    docx_res = analyze_final_document(b"not a valid docx", "bad.docx")
    assert docx_res["analyzed"] is False
    assert "DOCX 解析失败" in docx_res["reason"]

    pdf_res = analyze_final_document(b"not a valid pdf", "bad.pdf")
    assert pdf_res["analyzed"] is False
    assert "PDF 解析失败" in pdf_res["reason"]
