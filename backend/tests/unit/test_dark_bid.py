"""暗标导出：身份画像、全文脱敏、元数据清洗与审计。"""

from io import BytesIO

from docx import Document

from app.modules.generation.dark_bid import (
    NEUTRAL_BIDDER,
    apply_format_checks,
    audit_docx,
    build_identity,
    sanitize_document,
    scrub_metadata,
)


def _docx_bytes(document: Document) -> bytes:
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _inject_metadata(data: bytes, *, company: str, manager: str) -> bytes:
    """在 app.xml 写入单位/管理器，并补一个 custom.xml，模拟真实泄露场景。"""
    import re
    import zipfile

    buffer = BytesIO()
    with zipfile.ZipFile(BytesIO(data)) as source, zipfile.ZipFile(
        buffer, "w", zipfile.ZIP_DEFLATED
    ) as target:
        for item in source.infolist():
            payload = source.read(item.filename)
            if item.filename == "docProps/app.xml":
                text = payload.decode("utf-8")
                text = re.sub(r"<Company>.*?</Company>", f"<Company>{company}</Company>", text)
                text = re.sub(r"<Manager>.*?</Manager>", f"<Manager>{manager}</Manager>", text)
                payload = text.encode("utf-8")
            target.writestr(item, payload)
        target.writestr(
            "docProps/custom.xml",
            '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/custom-properties">'
            "<property><name>编制单位</name><value>北京华信科技有限公司</value></property>"
            "</Properties>",
        )
    return buffer.getvalue()


def _identity(**overrides):
    params = dict(
        company_name="北京华信科技有限公司",
        tenant_name="演示投标企业",
        credit_code="91110108MA01ABCD2X",
        contacts={"phone": "010-88886666", "email": "bid@huaxin.example.com"},
        personnel=[
            {"name": "张三", "cert_no": "BJ-2024-000123"},
            {"name": "李四", "cert_no": None},
        ],
        commitments=[],
    )
    params.update(overrides)
    return build_identity(**params)


def test_identity_covers_alias_credit_contact_and_cert() -> None:
    identity = _identity()
    terms = set(identity.forbidden_terms)
    assert "北京华信科技有限公司" in terms
    # 去掉「有限公司」后的简称也在保护范围
    assert "北京华信科技" in terms
    assert "演示投标企业" in terms
    assert "91110108MA01ABCD2X" in terms
    assert "010-88886666" in terms
    assert "BJ-2024-000123" in terms
    assert "张三" in terms


def test_identity_uses_commitment_role_for_personnel() -> None:
    class Commitment:
        kind = "personnel"
        value_text = "张三"
        label = "项目经理"

    identity = _identity(commitments=[Commitment()])
    mapping = dict(identity.replacements)
    assert mapping["张三"] == "项目经理"
    assert mapping["李四"] == "项目负责人"


def test_sanitize_removes_identity_self_reference_and_preserves_bold() -> None:
    document = Document()
    paragraph = document.add_paragraph()
    bold = paragraph.add_run("北京华信科技有限公司")
    bold.bold = True
    paragraph.add_run("（以下简称我公司）承担本项目，联系人张三，电话 010-88886666。")
    table = document.add_table(rows=1, cols=1)
    table.cell(0, 0).text = "投标人：北京华信科技，信用代码 91110108MA01ABCD2X"
    section = document.sections[0]
    section.header.paragraphs[0].text = "北京华信科技有限公司 技术标"
    section.footer.paragraphs[0].text = "北京华信科技 第 1 页"

    identity = _identity()
    sanitize_document(document, identity)

    body = "\n".join(item.text for item in document.paragraphs)
    assert "北京华信科技有限公司" not in body
    assert "北京华信科技" not in body
    assert "我公司" not in body
    assert "张三" not in body
    assert "010-88886666" not in body
    assert NEUTRAL_BIDDER in body
    assert "项目经理" in body or "项目负责人" in body
    # run 内命中时保留原格式
    assert document.paragraphs[0].runs[0].bold is True
    assert "北京华信" not in section.header.paragraphs[0].text
    assert "北京华信" not in section.footer.paragraphs[0].text
    assert "北京华信" not in table.cell(0, 0).text


def test_scrub_metadata_clears_author_company_and_custom_props() -> None:
    document = Document()
    document.add_paragraph("技术方案")
    document.core_properties.author = "张三"
    document.core_properties.last_modified_by = "李四"
    data = _inject_metadata(
        _docx_bytes(document), company="北京华信科技有限公司", manager="李四"
    )

    # 清洗前：审计必须能发现元数据泄露
    dirty = audit_docx(data, _identity())
    assert "ANBIAO-METADATA" in {item["code"] for item in dirty}

    cleaned = scrub_metadata(data)
    import zipfile

    with zipfile.ZipFile(BytesIO(cleaned)) as archive:
        core = archive.read("docProps/core.xml").decode("utf-8")
        app = archive.read("docProps/app.xml").decode("utf-8")
        names = set(archive.namelist())
        assert "张三" not in core and "李四" not in core
        assert "<dc:creator></dc:creator>" in core
        assert "北京华信科技有限公司" not in app and "李四" not in app
        assert "docProps/custom.xml" in names
        custom = archive.read("docProps/custom.xml").decode("utf-8")
        assert "北京华信科技有限公司" not in custom

    findings = audit_docx(cleaned, _identity())
    assert findings == []


def test_audit_detects_leaks_and_dirty_metadata() -> None:
    document = Document()
    document.add_paragraph("我公司承诺：由北京华信科技有限公司实施。")
    document.core_properties.author = "张三"
    findings = audit_docx(_docx_bytes(document), _identity())
    codes = {item["code"] for item in findings}
    assert "ANBIAO-IDENTITY" in codes
    assert "ANBIAO-SELF-REFERENCE" in codes
    assert "ANBIAO-METADATA" in codes


def test_apply_format_checks_reports_header_toc_and_font() -> None:
    document = Document()
    document.add_paragraph("目录")
    run = document.add_paragraph().add_run("施工组织设计")
    run.font.name = "黑体"
    document.sections[0].header.paragraphs[0].text = "某公司技术标"
    rules = [
        {"rule": "不得设置页眉页脚，不编页码，不得设目录；正文宋体四号，行距固定值 25 磅。"}
    ]
    findings = apply_format_checks(document, rules)
    keys = {item["rule"] for item in findings}
    assert {"页眉", "目录", "字体"} <= keys


def test_apply_format_checks_skips_when_rules_absent() -> None:
    document = Document()
    document.add_paragraph("技术方案")
    assert apply_format_checks(document, []) == []
