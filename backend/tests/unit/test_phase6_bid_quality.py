"""阶段六单元：暗标导出自检（元数据清空/泄漏拦截）、页边距对照修正、KB 跨库查重。"""

from io import BytesIO
from uuid import uuid4

from docx import Document
from docx.shared import Cm

from app.modules.compliance.engine import ComplianceContext, Rule, _check_kb_duplicate
from app.modules.generation.dark_bid import (
    audit_docx,
    build_identity,
    sanitize_document,
)
from app.modules.generation.service import (
    _fix_margins,
    _margin_deviations,
)


def _identity(*names: str):
    return build_identity(
        company_name=names[0] if names else None,
        tenant_name=None,
        credit_code=None,
        contacts=None,
        personnel=[],
    )


def _doc(author: str = "张三", body: str = "技术方案正文。") -> Document:
    document = Document()
    document.core_properties.author = author
    document.core_properties.last_modified_by = author
    document.add_paragraph(body)
    return document


def test_dark_bid_sanitize_clears_metadata() -> None:
    document = _doc(author="李工")
    sanitize_document(document, _identity())

    props = document.core_properties
    assert not str(props.author or "").strip()
    assert not str(props.last_modified_by or "").strip()


def test_dark_bid_audit_blocks_identity_leak() -> None:
    document = _doc(body="本项目由某云科技有限公司承建。")
    buffer = BytesIO()
    document.save(buffer)
    findings = audit_docx(buffer.getvalue(), _identity("某云科技有限公司"))
    assert any("某云科技有限公司" in item["message"] for item in findings)


def test_margin_deviations_detect_and_fix() -> None:
    document = Document()
    section = document.sections[0]
    section.top_margin = Cm(1.0)  # 期望 2.5 → 偏差
    section.bottom_margin = Cm(4.0)  # 期望 2.5 → 偏差
    rules = [{"rule": "页边距：上下2.5厘米，左右3厘米。"}]

    deviations = _margin_deviations(document, rules)
    positions = {item["position"] for item in deviations}
    assert {"上", "下"} <= positions  # 左 3.17cm 默认值 vs 3cm → 差 0.17 在容差内
    assert all(item["expected_cm"] == "2.50" for item in deviations if item["position"] in ("上", "下"))

    _fix_margins(document, deviations)
    assert _margin_deviations(document, rules) == []


def test_margin_rules_without_margin_text_are_ignored() -> None:
    document = Document()
    assert _margin_deviations(document, [{"rule": "正文宋体小四。"}]) == []


def _rule() -> Rule:
    return Rule(
        rule_id="SIMILARITY-KB-001",
        name="与知识库内容高度雷同",
        category="consistency",
        type="SOFT",
        severity="low",
        expect="改写",
        basis="查重",
        auto_fixable=False,
        needs_review=True,
    )


def _context(sections, kb_texts) -> ComplianceContext:
    return ComplianceContext(
        project=None,
        payload={},
        sections=sections,
        assets={},
        qualification_results=[],
        unsourced_claims=[],
        docx_document=None,
        docx_text="",
        content_text="",
        company_name=None,
        tenant_name=None,
        kb_texts=kb_texts,
    )


class _Section:
    def __init__(self, title: str, content_text: str) -> None:
        self.id = uuid4()
        self.title = title
        self.content_text = content_text


def test_kb_duplicate_flags_high_overlap_lines() -> None:
    kb_text = "本项目采用虚拟化技术构建统一政务云平台，实现资源池化与弹性伸缩，保障系统高可用运行。"
    section = _Section("技术方案", "总体架构上，本项目采用虚拟化技术构建统一政务云平台，实现资源池化与弹性伸缩，保障系统高可用运行。\n这一段是原创的叙述，与知识库内容完全不同。")
    findings = _check_kb_duplicate(_rule(), _context([section], [kb_text]))

    assert findings and "高度雷同" in findings[0]["message"]
    assert "虚拟化" in findings[0]["actual"]


def test_kb_duplicate_quiet_when_no_kb_or_no_overlap() -> None:
    section = _Section("技术方案", "本项目采用虚拟化技术构建统一政务云平台。")
    assert _check_kb_duplicate(_rule(), _context([section], [])) == []

    original = _Section("技术方案", "本项目的部署架构为两地三中心容灾设计，主备双活并配异地灾备副本。")
    assert _check_kb_duplicate(_rule(), _context([original], ["完全无关的知识库内容，讲的是人力资源管理制度。"])) == []


def _exported_bytes() -> bytes:
    buffer = BytesIO()
    _doc().save(buffer)
    return buffer.getvalue()
