"""最终标书文件的装配体检：封面/目录/页码/页数。

只做可机械核验的检查；页码在 PDF 上无法判定时如实标 unknown，交给人工。
"""
import contextlib
from io import BytesIO
from pathlib import Path
from typing import Any

from docx import Document as DocxDocument
from pypdf import PdfReader

SUPPORTED_SUFFIXES = {".docx", ".pdf"}


def analyze_final_document(
    content: bytes,
    filename: str,
    *,
    project_name: str = "",
) -> dict[str, Any]:
    suffix = Path(filename).suffix.lower()
    if suffix == ".docx":
        return _analyze_docx(content, project_name=project_name)
    if suffix == ".pdf":
        return _analyze_pdf(content, project_name=project_name)
    return {
        "analyzed": False,
        "reason": "仅支持 docx / pdf 做装配体检",
        "has_cover_name": None,
        "has_toc": None,
        "has_page_numbers": None,
        "page_count": None,
        "paragraph_count": None,
    }


def _analyze_docx(content: bytes, *, project_name: str) -> dict[str, Any]:
    try:
        document = DocxDocument(BytesIO(content))
    except Exception as exc:  # noqa: BLE001
        return {
            "analyzed": False,
            "format": "docx",
            "reason": f"DOCX 解析失败: {exc}",
            "has_cover_name": False,
            "has_toc": False,
            "has_page_numbers": False,
            "page_count": None,
            "paragraph_count": 0,
            "empty_paragraph_ratio": 0.0,
            "metadata": {},
        }
    paragraphs = [paragraph.text.strip() for paragraph in document.paragraphs]
    nonempty = [text for text in paragraphs if text]
    head_text = "\n".join(nonempty[:20])
    body_text = "\n".join(nonempty)
    has_cover_name: bool | None = None
    if project_name:
        has_cover_name = project_name in head_text
    has_toc = "目录" in body_text
    has_page_numbers = False
    for section in document.sections:
        footer_xml = ""
        header_xml = ""
        with contextlib.suppress(AttributeError):
            footer_xml = section.footer._element.xml
        with contextlib.suppress(AttributeError):
            header_xml = section.header._element.xml
        if (
            "PAGE" in footer_xml
            or "fldChar" in footer_xml
            or "PAGE" in header_xml
            or "fldChar" in header_xml
        ):
            has_page_numbers = True
            break
    return {
        "analyzed": True,
        "format": "docx",
        "has_cover_name": has_cover_name,
        "has_toc": has_toc,
        "has_page_numbers": has_page_numbers,
        "page_count": None,
        "paragraph_count": len(nonempty),
        "empty_paragraph_ratio": (
            round(1 - len(nonempty) / len(paragraphs), 3) if paragraphs else 0.0
        ),
        "metadata": {
            "author": document.core_properties.author,
            "last_modified_by": document.core_properties.last_modified_by,
            "revision": document.core_properties.revision,
            "title": document.core_properties.title,
        },
    }


def _analyze_pdf(content: bytes, *, project_name: str) -> dict[str, Any]:
    try:
        reader = PdfReader(BytesIO(content))
        pages = list(reader.pages)
    except Exception as exc:  # noqa: BLE001
        return {
            "analyzed": False,
            "format": "pdf",
            "reason": f"PDF 解析失败: {exc}",
            "has_cover_name": False,
            "has_toc": False,
            "has_page_numbers": None,
            "page_count": 0,
            "paragraph_count": None,
            "empty_paragraph_ratio": None,
            "metadata": {},
        }
    texts: list[str] = []
    for page in pages:
        try:
            texts.append(page.extract_text() or "")
        except Exception:  # noqa: BLE001 - PDF 解析失败不应中断体检
            texts.append("")
    body_text = "\n".join(texts)
    head_text = texts[0] if texts else ""
    has_cover_name: bool | None = None
    if project_name:
        has_cover_name = project_name in head_text
    # pypdf 的 metadata 是 Optional[DocumentInformation]（dict 子类），
    # `or {}` 会把空字典推断成 dict[Never, Never] 导致 .get 报错；统一转成 dict[str, Any]。
    metadata: dict[str, Any] = dict(reader.metadata or {})
    return {
        "analyzed": True,
        "format": "pdf",
        "has_cover_name": has_cover_name,
        "has_toc": "目录" in body_text,
        # PDF 页码域无法机械判定，如实标 unknown，交给人工核对
        "has_page_numbers": None,
        "page_count": len(pages),
        "paragraph_count": None,
        "empty_paragraph_ratio": None,
        "metadata": {
            "author": metadata.get("/Author"),
            "last_modified_by": metadata.get("/Creator"),
            "revision": None,
            "title": metadata.get("/Title"),
        },
    }
