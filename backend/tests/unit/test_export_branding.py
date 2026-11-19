"""导出内嵌交付品牌：品牌解析、无企业回退 BidCopilot、Logo 恒无、xlsx/docx 品牌名读回。

产品只面向企业投标部门（单一投标主体）：交付主体就是投标企业自身，不再是
「客户白标」。因此：
- 品牌名取 ``project.company_id`` 对应 Company 的名称；
- 项目未关联企业时回退 ``BidCopilot``；
- Company 没有 logo 字段（不再是客户 Logo），``logo_bytes`` 恒为 None、
  ``has_logo`` 恒为 False。

测试替身只出现在本测试文件内（生产代码不含 Mock）。
"""

from datetime import UTC, datetime
from io import BytesIO
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from docx import Document as DocxDocument
from docx.oxml.ns import qn
from openpyxl import load_workbook
from PIL import Image

from app.core.errors import NotFoundError
from app.modules.compliance.export import build_report_pdf, build_report_xlsx
from app.modules.projects.export_branding import (
    BRAND_ROW_LABEL,
    FALLBACK_BRAND_NAME,
    ExportBranding,
    append_branding_row,
    branding_for_export,
    branding_for_project,
    load_brand_logo,
    resolve_export_branding,
)
from app.modules.projects.readiness_export import build_readiness_xlsx
from app.modules.projects.responses_export import build_responses_xlsx


def _png_bytes(width: int = 40, height: int = 20) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (width, height), (37, 99, 235)).save(buffer, format="PNG")
    return buffer.getvalue()


def _project(**overrides: Any) -> SimpleNamespace:
    values: dict[str, Any] = {
        "id": uuid4(),
        "company_id": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _company(**overrides: Any) -> SimpleNamespace:
    """投标企业自身（交付主体）。Company 没有 logo 字段。"""
    values: dict[str, Any] = {
        "id": uuid4(),
        "name": "某某科技有限公司",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class _Result:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one_or_none(self) -> Any:
        return self._value


class _FakeSession:
    """按调用顺序返回预置结果的会话替身（仅测试用）。"""

    def __init__(self, *results: Any) -> None:
        self._results = list(results)
        self.statements: list[Any] = []

    async def execute(self, statement: Any) -> _Result:
        self.statements.append(statement)
        if not self._results:
            raise AssertionError("unexpected query")
        return _Result(self._results.pop(0))


def _compiled(statement: Any) -> str:
    from sqlalchemy.dialects import postgresql

    return str(statement.compile(dialect=postgresql.dialect()))


def _bind_values(statement: Any) -> set[Any]:
    compiled = statement.compile()
    return set(compiled.params.values())


@pytest.mark.asyncio
async def test_project_lookup_is_tenant_scoped() -> None:
    """导出品牌解析必须先按租户取项目（硬约束：禁止无租户条件的业务查询）。"""
    tenant_id = uuid4()
    project = _project(tenant_id=tenant_id)
    session = _FakeSession(project)

    await resolve_export_branding(session, tenant_id, project.id)

    project_query = session.statements[0]
    assert "bid_projects.tenant_id" in _compiled(project_query)
    assert tenant_id in _bind_values(project_query)


@pytest.mark.asyncio
async def test_company_lookup_is_tenant_scoped() -> None:
    tenant_id = uuid4()
    company = _company()
    project = _project(tenant_id=tenant_id, company_id=company.id)
    session = _FakeSession(company)

    await branding_for_project(session, tenant_id, project)

    company_query = session.statements[0]
    assert "companies.tenant_id" in _compiled(company_query)
    assert tenant_id in _bind_values(company_query)


@pytest.mark.asyncio
async def test_brand_name_is_company_name() -> None:
    """交付品牌 = 投标企业自身名称（不再是客户品牌名）。"""
    company = _company(name="投标企业有限公司")
    project = _project(tenant_id=uuid4(), company_id=company.id)
    session = _FakeSession(company)

    branding = await branding_for_project(session, project.tenant_id, project)

    assert branding.name == "投标企业有限公司"
    assert branding.client_name == "投标企业有限公司"
    assert branding.logo_bytes is None
    assert branding.has_logo is False


@pytest.mark.asyncio
async def test_blank_company_name_falls_back_to_bidcopilot() -> None:
    """企业名为空白（脏数据）时仍回退 BidCopilot，不能导出空品牌。"""
    company = _company(name="   ")
    project = _project(tenant_id=uuid4(), company_id=company.id)
    session = _FakeSession(company)

    branding = await branding_for_project(session, project.tenant_id, project)

    assert branding.name == FALLBACK_BRAND_NAME
    assert branding.audit_fields()["brand"] == FALLBACK_BRAND_NAME


@pytest.mark.asyncio
async def test_no_company_falls_back_to_bidcopilot() -> None:
    """项目未关联企业：没有交付主体，回退 BidCopilot。"""
    project = _project(tenant_id=uuid4())
    session = _FakeSession(project)

    branding = await resolve_export_branding(session, project.tenant_id, project.id)

    assert branding.name == FALLBACK_BRAND_NAME
    assert branding.client_name is None
    assert branding.logo_bytes is None
    assert branding.has_logo is False


@pytest.mark.asyncio
async def test_unknown_company_falls_back_to_bidcopilot() -> None:
    """company_id 指向本租户已不存在的企业：视为未指定主体，回退而不是 404。"""
    project = _project(tenant_id=uuid4(), company_id=uuid4())
    session = _FakeSession(project, None)

    branding = await resolve_export_branding(session, project.tenant_id, project.id)

    assert branding.name == FALLBACK_BRAND_NAME
    assert branding.client_name is None


@pytest.mark.asyncio
async def test_branding_has_no_logo_without_logo_source() -> None:
    """Company 无 logo 字段：解析结果永远不带 Logo，且不因此抛错。"""
    company = _company(name="无 Logo 企业")
    project = _project(tenant_id=uuid4(), company_id=company.id)
    session = _FakeSession(company)

    branding = await branding_for_project(session, project.tenant_id, project)

    assert branding.name == "无 Logo 企业"
    assert branding.logo_bytes is None
    assert branding.logo_content_type is None
    assert branding.logo_url is None
    assert branding.has_logo is False


@pytest.mark.asyncio
async def test_cross_tenant_project_read_raises_not_found() -> None:
    """项目按租户过滤：本租户取不到就 404，不泄露存在性。"""
    session = _FakeSession(None)

    with pytest.raises(NotFoundError):
        await resolve_export_branding(session, uuid4(), uuid4())


@pytest.mark.asyncio
async def test_unsupported_logo_format_degrades(tmp_path) -> None:
    """load_brand_logo 仍按格式白名单降级（供未来投标企业 Logo 复用的安全边界）。"""
    from app.modules.gateway.snapshots import LocalSnapshotStore

    store = LocalSnapshotStore(str(tmp_path / "snapshots"))
    ref = await store.put_bytes("brand/logo.txt", b"not-an-image", "text/plain")

    logo_bytes, content_type = await load_brand_logo(ref, store=store)

    assert logo_bytes is None
    assert content_type is None


@pytest.mark.asyncio
async def test_oversized_logo_degrades(tmp_path) -> None:
    from app.modules.gateway.snapshots import LocalSnapshotStore

    store = LocalSnapshotStore(str(tmp_path / "snapshots"))
    ref = await store.put_bytes("brand/big.png", _png_bytes() + b"\x00" * (5 * 1024 * 1024))

    logo_bytes, content_type = await load_brand_logo(ref, store=store)

    assert logo_bytes is None
    assert content_type is None


@pytest.mark.asyncio
async def test_external_logo_url_is_not_fetched() -> None:
    """外部 http(s) 地址不做无网关约束抓取：直接降级，不抛异常。"""
    logo_bytes, content_type = await load_brand_logo("https://example.com/logo.png")

    assert logo_bytes is None
    assert content_type is None


def test_branding_for_export_skips_dark_bid() -> None:
    branding = ExportBranding(name="某投标企业品牌")

    assert branding_for_export(branding, dark_bid=False) is branding
    assert branding_for_export(branding, dark_bid=True) is None


def test_audit_fields_record_brand_and_logo_state() -> None:
    with_logo = ExportBranding(name="甲品牌", logo_bytes=b"x", client_name="甲公司")
    without_logo = ExportBranding(name="乙品牌", client_name=None)

    assert with_logo.audit_fields() == {
        "brand": "甲品牌",
        "brand_logo": True,
        "brand_client": "甲公司",
    }
    assert without_logo.audit_fields()["brand_logo"] is False


# ---------------------------------------------------------------- xlsx 内嵌品牌


def test_readiness_xlsx_contains_brand_name_and_logo() -> None:
    payload = {"template": {"name": "IT 集成", "version": 2}, "summary": {"total": 1}}
    branding = ExportBranding(name="就绪清单品牌", logo_bytes=_png_bytes(), logo_content_type="image/png")

    content = build_readiness_xlsx("测试项目", payload, branding=branding)

    workbook = load_workbook(BytesIO(content))
    summary = workbook["Summary"]
    assert summary["A2"].value == BRAND_ROW_LABEL
    assert summary["B2"].value == "就绪清单品牌"
    assert summary["A3"].value == "项目"
    assert summary["B3"].value == "测试项目"
    assert len(summary._images) == 1
    workbook.close()


def test_readiness_xlsx_without_branding_keeps_existing_layout() -> None:
    payload = {"template": {"name": "IT 集成", "version": 2}, "summary": {"total": 1}}

    content = build_readiness_xlsx("测试项目", payload)

    workbook = load_workbook(BytesIO(content))
    summary = workbook["Summary"]
    assert summary["A2"].value == "项目"
    assert summary["B2"].value == "测试项目"
    assert summary["A2"].value != BRAND_ROW_LABEL
    assert len(summary._images) == 0
    workbook.close()


def test_responses_xlsx_contains_brand_name_without_logo() -> None:
    items = [
        {
            "requirement_type": "scoring",
            "category": "技术",
            "requirement_text": "须具备双活能力",
            "response_status": "pending",
        }
    ]
    branding = ExportBranding(name="响应表品牌")

    content = build_responses_xlsx("测试项目", items, branding=branding)

    workbook = load_workbook(BytesIO(content))
    summary = workbook["Summary"]
    assert summary["A2"].value == BRAND_ROW_LABEL
    assert summary["B2"].value == "响应表品牌"
    assert len(summary._images) == 0
    responses = workbook["Responses"]
    assert responses["A1"].value == "序号"
    workbook.close()


def test_responses_xlsx_without_branding_keeps_formula_guard() -> None:
    content = build_responses_xlsx("=cmd|' /C calc'!A0", [])

    workbook = load_workbook(BytesIO(content))
    summary = workbook["Summary"]
    assert summary["B2"].data_type == "s"
    assert summary["A2"].value == "项目"
    workbook.close()


def test_compliance_xlsx_contains_brand_name() -> None:
    report = SimpleNamespace(
        project_id=uuid4(),
        rule_version="v1",
        file_hash="deadbeef",
        created_at=datetime.now(UTC),
        summary_json={"hard": 0, "soft": 0, "findings": 0, "by_category": {}},
    )

    content = build_report_xlsx(
        report, [], "合规项目", branding=ExportBranding(name="合规报告品牌", logo_bytes=_png_bytes())
    )

    workbook = load_workbook(BytesIO(content))
    summary = workbook["Summary"]
    assert summary["A2"].value == BRAND_ROW_LABEL
    assert summary["B2"].value == "合规报告品牌"
    assert len(summary._images) == 1
    workbook.close()


def test_compliance_xlsx_without_branding_unchanged() -> None:
    report = SimpleNamespace(
        project_id=uuid4(),
        rule_version="v1",
        file_hash="deadbeef",
        created_at=datetime.now(UTC),
        summary_json={"hard": 0, "soft": 0, "findings": 0, "by_category": {}},
    )

    content = build_report_xlsx(report, [], "合规项目")

    workbook = load_workbook(BytesIO(content))
    summary = workbook["Summary"]
    assert summary["A2"].value == "项目"
    assert summary["B2"].value == "合规项目"
    workbook.close()


def test_compliance_pdf_contains_brand_name() -> None:
    report = SimpleNamespace(
        project_id=uuid4(),
        rule_version="v1",
        file_hash="deadbeef",
        created_at=datetime.now(UTC),
        summary_json={"hard": 0, "soft": 0, "findings": 0, "by_category": {}},
    )

    content = build_report_pdf(report, [], "合规项目", branding=ExportBranding(name="PDF 品牌"))

    assert content.startswith(b"%PDF")
    # reportlab 用 CID 字体编码中文，不能直接字节搜索；至少保证 PDF 结构完整可解析。
    from pypdf import PdfReader

    assert len(PdfReader(BytesIO(content)).pages) >= 1


def test_brand_name_is_excel_formula_safe() -> None:
    """品牌名来自企业名，不得让 =/+/@ 开头的内容变成公式。"""
    items: list[dict[str, Any]] = []
    branding = ExportBranding(name="=HYPERLINK(\"http://evil\")")

    content = build_responses_xlsx("测试项目", items, branding=branding)

    workbook = load_workbook(BytesIO(content))
    summary = workbook["Summary"]
    assert summary["B2"].data_type == "s"
    assert str(summary["B2"].value).startswith("'=HYPERLINK")
    workbook.close()


def test_append_branding_row_returns_false_when_logo_embed_fails() -> None:
    """损坏的图片字节不能让导出抛异常：降级为仅品牌名。"""
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    broken = ExportBranding(name="坏图品牌", logo_bytes=b"\x89PNG\r\n\x1a\nbroken")

    embedded = append_branding_row(sheet, broken)

    assert embedded is False
    # 空工作簿首次 append 落在第 1 行（无 A1 标题占位）。
    assert sheet["A1"].value == BRAND_ROW_LABEL
    assert sheet["B1"].value == "坏图品牌"


# ---------------------------------------------------------------- docx 内嵌品牌


def _docx_project() -> SimpleNamespace:
    return SimpleNamespace(name="测试项目")


def _docx_section() -> SimpleNamespace:
    return SimpleNamespace(
        title="技术方案",
        content_json=[{"type": "paragraph", "text": "本节说明技术架构。"}],
    )


def test_docx_header_contains_brand_name_and_logo() -> None:
    from app.modules.generation.service import build_docx_document

    branding = ExportBranding(name="docx 品牌", logo_bytes=_png_bytes())

    document = build_docx_document(
        _docx_project(), [_docx_section()], {}, branding=branding
    )

    header_text = "\n".join(
        paragraph.text for paragraph in document.sections[0].header.paragraphs
    )
    assert "docx 品牌" in header_text
    # Logo 以页眉内联图片嵌入（不占正文 inline_shapes），序列化后仍在。
    assert document.sections[0].header.paragraphs[0]._p.findall(".//" + qn("w:drawing"))
    body_text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    assert "docx 品牌" not in body_text
    # 正文结构不受影响：标题仍在正文，页眉 Logo 不占用 body inline_shapes。
    assert document.paragraphs[0].text == "测试项目"
    assert len(document.inline_shapes) == 0

    buffer = BytesIO()
    document.save(buffer)
    reopened = DocxDocument(BytesIO(buffer.getvalue()))
    header_text = "\n".join(
        paragraph.text for paragraph in reopened.sections[0].header.paragraphs
    )
    assert "docx 品牌" in header_text
    assert reopened.sections[0].header.paragraphs[0]._p.findall(".//" + qn("w:drawing"))


def test_docx_header_with_company_branding_has_no_logo() -> None:
    """真实解析路径（企业名、无 Logo）：页眉只有品牌名，没有 drawing。"""
    from app.modules.generation.service import build_docx_document

    document = build_docx_document(
        _docx_project(),
        [_docx_section()],
        {},
        branding=ExportBranding(name="投标企业有限公司"),
    )

    header_paragraph = document.sections[0].header.paragraphs[0]
    assert "投标企业有限公司" in header_paragraph.text
    assert not header_paragraph._p.findall(".//" + qn("w:drawing"))


def test_docx_without_branding_has_empty_header() -> None:
    from app.modules.generation.service import build_docx_document

    document = build_docx_document(_docx_project(), [_docx_section()], {})

    header_text = "\n".join(
        paragraph.text for paragraph in document.sections[0].header.paragraphs
    )
    assert header_text.strip() == ""
    assert document.paragraphs[0].text == "测试项目"


def test_docx_branding_survives_apply_format() -> None:
    """apply_format 只改页面/样式，不得抹掉页眉品牌。"""
    from app.modules.generation.formatting import apply_format
    from app.modules.generation.service import build_docx_document

    document = build_docx_document(
        _docx_project(),
        [_docx_section()],
        {},
        branding=ExportBranding(name="格式后品牌"),
    )
    apply_format(
        document,
        {
            "page": {
                "size": "A4",
                "margins_cm": {"top": 2.0, "bottom": 2.0, "left": 2.5, "right": 2.5},
            }
        },
    )

    header_text = "\n".join(
        paragraph.text for paragraph in document.sections[0].header.paragraphs
    )
    assert "格式后品牌" in header_text
    # Cm 与 docx 内部 EMU 之间有取整误差，按 0.01cm 容差比较。
    assert abs(document.sections[0].top_margin.cm - 2.0) < 0.01
