from types import SimpleNamespace

from docx.enum.table import WD_ALIGN_VERTICAL, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn

from app.modules.generation.service import build_docx_document

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)


def test_build_docx_renders_styled_table_and_image() -> None:
    section = SimpleNamespace(
        title="技术方案",
        content_json=[
            {"type": "paragraph", "text": "本节说明技术架构。"},
            {
                "type": "table",
                "caption": "评分项响应表",
                "headers": ["评分项", "分值"],
                "rows": [["技术方案", "40"]],
            },
            {"type": "image", "image_id": "img-1", "caption": "双活架构"},
        ],
    )
    project = SimpleNamespace(name="测试项目")
    document = build_docx_document(project, [section], {"img-1": (PNG, "双活架构")})

    assert len(document.tables) == 1
    table = document.tables[0]
    assert table.style.name == "Table Grid"
    assert table.alignment == WD_TABLE_ALIGNMENT.CENTER
    assert table.autofit is False
    assert table.rows[0].cells[0].text == "评分项"
    assert table.rows[0].cells[0].paragraphs[0].alignment == WD_ALIGN_PARAGRAPH.CENTER
    assert table.rows[1].cells[1].paragraphs[0].alignment == WD_ALIGN_PARAGRAPH.RIGHT
    assert table.rows[1].cells[0].paragraphs[0].alignment != WD_ALIGN_PARAGRAPH.RIGHT
    assert table.rows[1].cells[0].vertical_alignment == WD_ALIGN_VERTICAL.CENTER
    assert all(row._tr.find(qn("w:trPr")).find(qn("w:cantSplit")) is not None for row in table.rows)
    assert len(document.inline_shapes) == 1
    assert any("表 1-1 评分项响应表" in paragraph.text for paragraph in document.paragraphs)
    assert any("图 1-1 双活架构" in paragraph.text for paragraph in document.paragraphs)
