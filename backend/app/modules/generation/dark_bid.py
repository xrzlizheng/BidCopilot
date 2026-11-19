"""暗标（盲评）导出：身份画像 → 全文脱敏 → 元数据清洗 → 导出前审计。

定位：按招标文件暗标要求移除投标人可识别信息，属于合规交付；不做相似度/串标
检测的规避，也不改写业务事实。规则依据见 docs/product/2026-11-02-anbiao-blind-review-rules.md。
"""

import re
import zipfile
from dataclasses import dataclass
from io import BytesIO
from typing import Any

from docx.oxml.ns import qn

NEUTRAL_BIDDER = "投标人"
NEUTRAL_PERSON = "项目负责人"
CERT_MASK = "（证书编号略）"
CREDIT_MASK = "（统一社会信用代码略）"
CONTACT_MASK = "（联系方式略）"
ADDRESS_MASK = "（地址略）"

SELF_REFERENCE = re.compile(r"我公司|本公司|我司|本单位|我单位|我方|我集团|我企业")
LEGAL_SUFFIXES = (
    "股份有限公司",
    "有限责任公司",
    "有限公司",
    "集团有限公司",
    "集团",
    "公司",
)
# 联系人字段的常见键名（来自工商/手工录入，结构不固定，按需取值）。
CONTACT_KEYS = (
    "phone",
    "telephone",
    "mobile",
    "tel",
    "email",
    "mail",
    "website",
    "site",
    "url",
    "fax",
    "address",
    "reg_address",
    "registered_address",
    "legal_representative",
    "legal_person",
)
ADDRESS_KEYS = {"address", "reg_address", "registered_address"}

# 字号（中文字号 → 磅）。
FONT_SIZE_PT = {
    "初号": 42,
    "小初": 36,
    "一号": 26,
    "小一": 24,
    "二号": 22,
    "小二": 18,
    "三号": 16,
    "小三": 15,
    "四号": 14,
    "小四": 12,
    "五号": 10.5,
    "小五": 9,
}


@dataclass(frozen=True)
class DarkBidIdentity:
    # 按长度降序，保证「北京华信科技有限公司」先于「北京华信科技」被替换。
    replacements: tuple[tuple[str, str], ...]
    self_reference_pattern: re.Pattern[str]
    self_reference_replacement: str
    forbidden_terms: tuple[str, ...]
    personals: tuple[str, ...]


def _aliases(name: str) -> list[str]:
    """公司名去掉法定后缀后的常见简称；过短不外扩，避免误伤通用词。"""
    aliases: list[str] = []
    for suffix in LEGAL_SUFFIXES:
        if name.endswith(suffix) and len(name) - len(suffix) >= 4:
            stem = name[: -len(suffix)]
            if stem not in aliases:
                aliases.append(stem)
            break
    return aliases


def _contact_values(contacts: dict[str, Any] | None) -> list[tuple[str, str]]:
    if not contacts:
        return []
    pairs: list[tuple[str, str]] = []
    for key in CONTACT_KEYS:
        value = contacts.get(key)
        if not isinstance(value, str):
            continue
        text = value.strip()
        if len(text) < 4:
            continue
        mask = ADDRESS_MASK if key in ADDRESS_KEYS else CONTACT_MASK
        pairs.append((text, mask))
    return pairs


def _role_by_name(commitments: list[Any]) -> dict[str, str]:
    roles: dict[str, str] = {}
    for commitment in commitments:
        if getattr(commitment, "kind", "") != "personnel":
            continue
        name = (getattr(commitment, "value_text", "") or "").strip()
        label = (getattr(commitment, "label", "") or "").strip()
        if name:
            roles[name] = label or NEUTRAL_PERSON
    return roles


def build_identity(
    *,
    company_name: str | None,
    tenant_name: str | None,
    credit_code: str | None,
    contacts: dict[str, Any] | None,
    personnel: list[dict[str, Any]],
    commitments: list[Any] | None = None,
) -> DarkBidIdentity:
    roles = _role_by_name(commitments or [])
    mapping: dict[str, str] = {}
    if company_name:
        mapping[company_name] = NEUTRAL_BIDDER
        for alias in _aliases(company_name):
            mapping.setdefault(alias, NEUTRAL_BIDDER)
    if tenant_name and tenant_name != company_name:
        mapping[tenant_name] = NEUTRAL_BIDDER
    personals: list[str] = []
    for person in personnel:
        name = str(person.get("name") or "").strip()
        if len(name) < 2:
            continue
        personals.append(name)
        mapping.setdefault(name, roles.get(name, NEUTRAL_PERSON))
        cert_no = str(person.get("cert_no") or "").strip()
        if len(cert_no) >= 6:
            mapping.setdefault(cert_no, CERT_MASK)
    if credit_code and len(credit_code.strip()) >= 6:
        mapping[credit_code.strip()] = CREDIT_MASK
    for value, mask in _contact_values(contacts):
        mapping.setdefault(value, mask)
    # 长度降序：先替换长名称，避免短名称先命中把长名称切碎。
    ordered = tuple(
        sorted(
            ((old, new) for old, new in mapping.items() if old and old != new),
            key=lambda item: (-len(item[0]), item[0]),
        )
    )
    forbidden = tuple(old for old, _ in ordered if not old.startswith("（"))
    return DarkBidIdentity(
        replacements=ordered,
        self_reference_pattern=SELF_REFERENCE,
        self_reference_replacement=NEUTRAL_BIDDER,
        forbidden_terms=forbidden,
        personals=tuple(personals),
    )


def _replace_text(text: str, identity: DarkBidIdentity) -> str:
    updated = text
    for old, new in identity.replacements:
        if old in updated:
            updated = updated.replace(old, new)
    return identity.self_reference_pattern.sub(identity.self_reference_replacement, updated)


def _replace_in_paragraph(paragraph: Any, identity: DarkBidIdentity) -> None:
    """优先在 run 内替换以保留字体格式；跨 run 命中时退化为整段重写。

    泄漏优先于格式：跨 run 的情况很少（公司名被加粗等），重写后仍有导出前审计兜底。
    """
    original = paragraph.text
    if not original:
        return
    updated = _replace_text(original, identity)
    if updated == original:
        return
    for run in paragraph.runs:
        replaced = _replace_text(run.text, identity)
        if replaced != run.text:
            run.text = replaced
    if paragraph.text == updated:
        return
    # 跨 run 命中（或替换后仍不一致）：整段重写，保证不残留身份信息。
    for run in list(paragraph.runs):
        run._element.getparent().remove(run._element)
    paragraph.add_run(updated)


def _paragraphs_in(element: Any) -> list[Any]:
    from docx.text.paragraph import Paragraph

    # python-docx 运行库允许 parent=None（游离段落），但其 stub 要求
    # ProvidesStoryPart；这里显式忽略并保留 None 语义。
    return [Paragraph(node, None) for node in element.iter(qn("w:p"))]  # type: ignore[arg-type]


def sanitize_document(document: Any, identity: DarkBidIdentity) -> None:
    """正文、表格、页眉页脚、文本框：替换身份名、证书号、联系方式与自指表述。"""
    for paragraph in document.paragraphs:
        _replace_in_paragraph(paragraph, identity)
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                for paragraph in cell.paragraphs:
                    _replace_in_paragraph(paragraph, identity)
    for section in document.sections:
        for part in (section.header, section.footer):
            for paragraph in part.paragraphs:
                _replace_in_paragraph(paragraph, identity)
            for table in part.tables:
                for row in table.rows:
                    for cell in row.cells:
                        for paragraph in cell.paragraphs:
                            _replace_in_paragraph(paragraph, identity)
            # 文本框等嵌套段落：直接按 XML 文本节点替换（无跨节点匹配时有效）。
            for paragraph in _paragraphs_in(part._element):
                _replace_in_paragraph(paragraph, identity)
    for paragraph in _paragraphs_in(document.element.body):
        _replace_in_paragraph(paragraph, identity)
    # 兜底：直接替换主文档/页眉页脚里的文本节点与域代码（覆盖超链接内文字）。
    elements = [document.element.body]
    for section in document.sections:
        elements.extend([section.header._element, section.footer._element])
    for element in elements:
        for tag in ("w:t", "w:instrText"):
            for node in element.iter(qn(tag)):
                if node.text:
                    node.text = _replace_text(node.text, identity)
    props = document.core_properties
    props.author = ""
    props.last_modified_by = ""
    props.title = "投标文件"
    props.subject = ""
    props.comments = ""
    props.keywords = ""
    props.category = ""
    props.content_status = ""
    props.identifier = ""
    props.language = "zh-CN"
    props.version = ""


def _rewrite_core_xml(data: bytes) -> bytes:
    pattern = re.compile(r"<cp:coreProperties[^>]*>.*?</cp:coreProperties>", re.S)
    replacement = (
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
        'xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        "<dc:title>投标文件</dc:title>"
        "<dc:creator></dc:creator>"
        "<cp:lastModifiedBy></cp:lastModifiedBy>"
        "<cp:revision>1</cp:revision>"
        "<dc:language>zh-CN</dc:language>"
        "</cp:coreProperties>"
    )
    return pattern.sub(replacement, data.decode("utf-8")).encode("utf-8")


def _rewrite_app_xml(data: bytes) -> bytes:
    text = data.decode("utf-8")

    def _blank(tag: str) -> None:
        nonlocal text
        text = re.sub(rf"<{tag}>.*?</{tag}>", f"<{tag}></{tag}>", text, flags=re.S)

    for tag in ("Company", "Manager", "CompanyEmail", "HyperlinkBase", "Template"):
        _blank(tag)
    return text.encode("utf-8")


# 1x1 透明 PNG：保留缩略图关系与 Content-Type，但不再暴露封面内容。
BLANK_THUMBNAIL = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000154a24f5f0000000049454e44ae426082"
)
EMPTY_CUSTOM_XML = (
    b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    b'<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/custom-properties" '
    b'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"/>'
)


def scrub_metadata(data: bytes) -> bytes:
    """重写 docProps：清空作者/单位/管理器、缩略图与自定义属性。"""
    buffer = BytesIO()
    with zipfile.ZipFile(BytesIO(data)) as source, zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as target:
        for item in source.infolist():
            name = item.filename
            payload = source.read(name)
            if name in {"docProps/thumbnail.jpeg", "docProps/thumbnail.jpg"}:
                # 不能直接删：_rels/.rels 仍引用该部件，删除会让 python-docx 打不开。
                payload = BLANK_THUMBNAIL
            elif name == "docProps/custom.xml":
                payload = EMPTY_CUSTOM_XML
            elif name == "docProps/core.xml":
                payload = _rewrite_core_xml(payload)
            elif name == "docProps/app.xml":
                payload = _rewrite_app_xml(payload)
            target.writestr(item, payload)
    return buffer.getvalue()


def _part_texts(data: bytes) -> dict[str, str]:
    texts: dict[str, str] = {}
    with zipfile.ZipFile(BytesIO(data)) as archive:
        for name in archive.namelist():
            if not name.endswith(".xml"):
                continue
            if not (
                name.startswith("word/")
                or name.startswith("docProps/")
                or name.startswith("customXml/")
            ):
                continue
            try:
                raw = archive.read(name).decode("utf-8", errors="ignore")
            except KeyError:
                continue
            texts[name] = raw
    return texts


def _strip_tags(xml: str) -> str:
    return re.sub(r"<[^>]+>", "", xml)


def audit_docx(data: bytes, identity: DarkBidIdentity) -> list[dict[str, Any]]:
    """重开导出文件做最终审计：身份串、自指表述、未清空元数据。"""
    findings: list[dict[str, Any]] = []
    parts = _part_texts(data)
    for part, xml in parts.items():
        if part.startswith("word/"):
            text = _strip_tags(xml)
            for term in identity.forbidden_terms:
                if term in text:
                    findings.append(
                        {
                            "code": "ANBIAO-IDENTITY",
                            "severity": "high",
                            "part": part,
                            "message": f"导出文件仍含身份信息：{term}",
                        }
                    )
            match = identity.self_reference_pattern.search(text)
            if match:
                findings.append(
                    {
                        "code": "ANBIAO-SELF-REFERENCE",
                        "severity": "high",
                        "part": part,
                        "message": f"导出文件仍含自指表述：{match.group(0)}",
                    }
                )
        elif part == "docProps/core.xml":
            for field, label in (
                ("dc:creator", "作者"),
                ("cp:lastModifiedBy", "最后修改者"),
            ):
                if re.search(rf"<{field}>\s*[^<\s]", xml):
                    findings.append(
                        {
                            "code": "ANBIAO-METADATA",
                            "severity": "high",
                            "part": part,
                            "message": f"文档元数据未清空：{label}",
                        }
                    )
        elif part == "docProps/app.xml":
            for tag, label in (("Company", "单位"), ("Manager", "管理器")):
                match = re.search(rf"<{tag}>\s*([^<]+?)\s*</{tag}>", xml)
                if match:
                    findings.append(
                        {
                            "code": "ANBIAO-METADATA",
                            "severity": "high",
                            "part": part,
                            "message": f"文档元数据未清空：{label}={match.group(1)}",
                        }
                    )
    return findings


def _rule_text(format_rules: list[dict[str, Any]]) -> str:
    return " ".join(str(rule.get("rule") or rule.get("text") or "") for rule in format_rules)


def _dominant_font(document: Any) -> tuple[str | None, float | None]:
    names: dict[str, int] = {}
    sizes: dict[float, int] = {}
    for paragraph in document.paragraphs:
        for run in paragraph.runs:
            if run.font.name:
                names[run.font.name] = names.get(run.font.name, 0) + len(run.text)
            if run.font.size is not None:
                point = round(run.font.size.pt, 1)
                sizes[point] = sizes.get(point, 0) + len(run.text)
    # 用下标函数而非 dict.get：mypy 无法把重载的 names.get 当作 max 的 key。
    name = max(names, key=lambda item: names[item]) if names else None
    size = max(sizes, key=lambda item: sizes[item]) if sizes else None
    return name, size


def apply_format_checks(document: Any, format_rules: list[dict[str, Any]]) -> list[dict[str, str]]:
    """按招标文件格式条款做确定性核对；只报告，不改写（页边距另有修正逻辑）。"""
    rules = _rule_text(format_rules)
    if not rules:
        return []
    findings: list[dict[str, str]] = []

    def add(rule: str, expected: str, actual: str) -> None:
        findings.append({"rule": rule, "expected": expected, "actual": actual})

    header_text = " ".join(
        paragraph.text.strip()
        for section in document.sections
        for paragraph in section.header.paragraphs
    ).strip()
    footer_text = " ".join(
        paragraph.text.strip()
        for section in document.sections
        for paragraph in section.footer.paragraphs
    ).strip()
    if ("不设页眉" in rules or "不得设置页眉" in rules or "不得设页眉" in rules) and header_text:
        add("页眉", "不得设置页眉", header_text[:60])
    if ("不设页脚" in rules or "不得设置页脚" in rules or "不得设页脚" in rules) and footer_text:
        add("页脚", "不得设置页脚", footer_text[:60])
    if ("不编页码" in rules or "不设页码" in rules or "不得设置页码" in rules) and (
        "PAGE" in footer_text or "PAGE" in header_text
    ):
        add("页码", "不得设置页码", footer_text[:60] or header_text[:60])
    if ("不得设目录" in rules or "不设目录" in rules) and "目录" in " ".join(
        paragraph.text for paragraph in document.paragraphs
    ):
        add("目录", "不得设置目录", "检测到目录页")
    font_name, font_size = _dominant_font(document)
    if "宋体" in rules and font_name and font_name != "宋体":
        add("字体", "宋体", font_name)
    for label, point in FONT_SIZE_PT.items():
        if label in rules and font_size is not None and abs(font_size - point) > 0.6:
            add("字号", label, f"{font_size} 磅")
            break
    if "固定值" in rules:
        spacing = next(
            (
                paragraph.paragraph_format.line_spacing
                for paragraph in document.paragraphs
                if paragraph.paragraph_format.line_spacing is not None
            ),
            None,
        )
        match = re.search(r"固定值\s*(\d+(?:\.\d+)?)\s*磅", rules)
        # python-docx：固定行距返回 Length（EMU），倍数返回 float。
        actual_pt = (
            spacing.pt if spacing is not None and hasattr(spacing, "pt") else spacing
        )
        if match and actual_pt is not None and abs(float(actual_pt) - float(match.group(1))) > 0.6:
            add("行距", f"固定值 {match.group(1)} 磅", f"{actual_pt} 磅")
    return findings
