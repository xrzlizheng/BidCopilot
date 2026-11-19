import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from app.core.time import local_date
from app.modules.compliance.consistency import (
    check_consistency_numbers,
    check_deposit_limit,
    check_open_responses,
    check_package_selection,
    check_quote_arithmetic,
    check_unlinked_responses,
)
from app.modules.compliance.registry import Rule
from app.modules.compliance.workload import check_workload_cost

FONT_HINTS = ("宋体", "仿宋", "黑体", "楷体", "微软雅黑", "Times New Roman", "Arial")
SIZE_HINTS = {
    "小四": 12,
    "五号": 10.5,
    "四号": 14,
    "小三": 15,
    "三号": 16,
    "二号": 22,
    "小五": 9,
}


@dataclass
class ComplianceContext:
    project: Any
    payload: dict[str, Any]
    sections: list[Any]
    assets: dict[str, list[dict[str, Any]]]
    qualification_results: list[Any]
    unsourced_claims: list[dict[str, Any]]
    docx_document: Any
    docx_text: str
    content_text: str
    company_name: str | None
    tenant_name: str | None
    knowledge_images: dict[str, Any] = field(default_factory=dict)
    responses: list[Any] = field(default_factory=list)
    kb_texts: list[str] = field(default_factory=list)
    cost_model: dict[str, Any] = field(default_factory=dict)
    final_assembly: dict[str, Any] = field(default_factory=dict)


def _finding(
    rule: Rule,
    *,
    message: str,
    location: dict[str, Any] | None = None,
    expected: str | None = None,
    actual: str | None = None,
    basis: str | None = None,
    suggestion: str | None = None,
    evidence_ref: str | None = None,
    needs_review: bool | None = None,
) -> dict[str, Any]:
    return {
        "rule_id": rule.rule_id,
        "category": rule.category,
        "severity": rule.severity,
        "finding_type": rule.type,
        "message": message,
        "location": location or {},
        "expected": expected or rule.expect,
        "actual": actual,
        "basis": basis or rule.basis,
        "suggestion": suggestion,
        "auto_fixable": rule.auto_fixable,
        "needs_review": rule.needs_review if needs_review is None else needs_review,
        "evidence_ref": evidence_ref,
    }


def _docx_paragraphs(document: Any) -> list[Any]:
    paragraphs = list(document.paragraphs)
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                paragraphs.extend(cell.paragraphs)
    for section in document.sections:
        paragraphs.extend(section.header.paragraphs)
        paragraphs.extend(section.footer.paragraphs)
    return paragraphs


def _docx_runs(document: Any) -> list[Any]:
    runs = []
    for paragraph in _docx_paragraphs(document):
        runs.extend(paragraph.runs)
    return runs


def _expected_fonts(payload: dict[str, Any]) -> list[str]:
    text = " ".join(
        str((rule.get("expect") or {}).get("text") or "")
        for rule in payload.get("format_rules", [])
    )
    return [font for font in FONT_HINTS if font in text]


def _expected_sizes(payload: dict[str, Any]) -> list[float]:
    text = " ".join(
        str((rule.get("expect") or {}).get("text") or "")
        for rule in payload.get("format_rules", [])
    )
    return [size for keyword, size in SIZE_HINTS.items() if keyword in text]


def _check_dark_names(rule: Rule, context: ComplianceContext) -> list[dict[str, Any]]:
    if not (context.payload.get("dark_bid") or {}).get("required"):
        return []
    names = [context.company_name, context.tenant_name]
    names.extend(str(asset.get("name")) for asset in context.assets.get("personnel", []))
    combined = f"{context.content_text}\n{context.docx_text}"
    findings = []
    for name in dict.fromkeys(name for name in names if name and len(name) >= 2):
        if name in combined:
            findings.append(
                _finding(
                    rule,
                    message=f"暗标文件中出现身份信息：{name}",
                    location={"scope": "document"},
                    actual=name,
                    suggestion="删除或替换为中性称呼，并重新导出暗标文件。",
                )
            )
    return findings


def _check_dark_meta(rule: Rule, context: ComplianceContext) -> list[dict[str, Any]]:
    if not (context.payload.get("dark_bid") or {}).get("required"):
        return []
    props = context.docx_document.core_properties
    fields = ("author", "last_modified_by", "comments", "category", "subject", "keywords")
    populated = [field for field in fields if str(getattr(props, field, "") or "").strip()]
    if not populated:
        return []
    return [
        _finding(
            rule,
            message="docx 核心属性包含可识别信息",
            location={"scope": "docx_metadata"},
            actual="、".join(populated),
            suggestion="清空作者、最后修改者、备注等元数据后重新导出。",
        )
    ]


def _check_dark_images(rule: Rule, context: ComplianceContext) -> list[dict[str, Any]]:
    if not (context.payload.get("dark_bid") or {}).get("required"):
        return []
    images = getattr(context, "knowledge_images", {}) or {}
    identities = [
        name
        for name in (
            context.company_name,
            context.tenant_name,
            *[str(asset.get("name")) for asset in context.assets.get("personnel", [])],
        )
        if name and len(str(name)) >= 2
    ]
    findings: list[dict[str, Any]] = []
    for section in context.sections:
        for block in section.content_json or []:
            if not isinstance(block, dict) or block.get("type") != "image":
                continue
            image = images.get(str(block.get("image_id")))
            location = {"section_id": str(section.id), "title": section.title}
            if image is None:
                findings.append(
                    _finding(
                        rule,
                        message="引用的知识库图片已不存在",
                        location=location,
                        actual=str(block.get("image_id")),
                        suggestion="重新选择图片或删除该图片块。",
                    )
                )
                continue
            haystack = " ".join(
                [
                    image.title or "",
                    image.caption or "",
                    image.filename or "",
                    *[str(tag) for tag in (image.tags or [])],
                ]
            )
            reasons: list[str] = []
            if image.sensitive:
                reasons.append("敏感标记")
            if any(str(name) in haystack for name in identities):
                reasons.append("含企业/人员名")
            if reasons:
                findings.append(
                    _finding(
                        rule,
                        message=f"暗标导出将排除图片：{image.title}（{'、'.join(reasons)}）",
                        location={**location, "image_id": str(image.id)},
                        actual=image.title,
                        suggestion="如确需保留，请脱敏后重新上传并取消敏感标记；否则确认移除。",
                    )
                )
    return findings


def _check_format_font(rule: Rule, context: ComplianceContext) -> list[dict[str, Any]]:
    expected = _expected_fonts(context.payload)
    if not expected:
        return []
    actual = {run.font.name for run in _docx_runs(context.docx_document) if run.font.name}
    if actual & set(expected):
        return []
    return [
        _finding(
            rule,
            message="正文字体与招标要求不一致",
            location={"scope": "docx"},
            expected="、".join(expected),
            actual="、".join(sorted(actual)) or "未显式设置",
            suggestion="按招标文件格式要求设置正文字体后重新导出。",
        )
    ]


def _check_format_size(rule: Rule, context: ComplianceContext) -> list[dict[str, Any]]:
    expected = _expected_sizes(context.payload)
    if not expected:
        return []
    actual = {
        float(run.font.size.pt)
        for run in _docx_runs(context.docx_document)
        if run.font.size is not None
    }
    if any(abs(size - expected_size) < 0.1 for size in actual for expected_size in expected):
        return []
    return [
        _finding(
            rule,
            message="正文字号与招标要求不一致",
            location={"scope": "docx"},
            expected="、".join(f"{size}pt" for size in expected),
            actual="、".join(f"{size}pt" for size in sorted(actual)) or "未显式设置",
            suggestion="按招标文件格式要求设置正文字号后重新导出。",
        )
    ]


def _check_qualification(
    rule: Rule, context: ComplianceContext, status: str
) -> list[dict[str, Any]]:
    findings = []
    for result in context.qualification_results:
        if result.status != status:
            continue
        findings.append(
            _finding(
                rule,
                message=f"资格要求 {result.status}：{result.text[:120]}",
                location=result.source_span or {"scope": "qualification"},
                expected="PASS",
                actual=f"{result.status}，{result.reason}",
                suggestion=(
                    "补齐缺失资质/人员/业绩材料后重新预审。"
                    if status == "UNKNOWN"
                    else "先完成资格整改，再提交投标文件。"
                ),
                evidence_ref=",".join(result.matched_asset_ids) or None,
            )
        )
    return findings[:100]


def _check_unsourced(rule: Rule, context: ComplianceContext) -> list[dict[str, Any]]:
    findings = []
    for item in context.unsourced_claims:
        findings.append(
            _finding(
                rule,
                message=f"无源事实主张：{item['claim']}",
                location={
                    "section_id": item.get("section_id"),
                    "section_title": item.get("section_title"),
                },
                actual=item["claim"],
                suggestion="在章节中补充来源，或删除/改写该主张。",
                evidence_ref=item.get("evidence_ref"),
            )
        )
    return findings[:200]


def _checklist_tokens(text: str) -> list[str]:
    cleaned = re.sub(r"^.*?(包括|包含|提交|提供|须提交|应提交)", "", text)
    tokens = [
        token.strip().strip("。；;：:，,")
        for token in re.split(r"[、,，/]", cleaned)
        if token.strip()
    ]
    return [token for token in tokens if 2 <= len(token) <= 40]


PLACEHOLDER_PATTERN = re.compile(
    r"(某市|某单位|某公司|某项目|某县|XX+|xx+|待填|【[^】]{0,30}】)"
)


def _check_placeholder(rule: Rule, context: ComplianceContext) -> list[dict[str, Any]]:
    text = context.content_text or ""
    if not text.strip():
        return []
    protected = " ".join(
        str(value or "")
        for value in (
            getattr(context.project, "name", ""),
            getattr(context.project, "buyer", ""),
            context.company_name,
        )
    )
    seen: list[str] = []
    for match in PLACEHOLDER_PATTERN.finditer(text):
        value = match.group(0).strip()
        if not value or value in protected or value in seen:
            continue
        seen.append(value)
        if len(seen) >= 10:
            break
    return [
        _finding(
            rule,
            message=f"疑似模板占位符：{value}",
            location={"scope": "content"},
            expected="替换为本项目真实名称/参数",
            actual=value,
            suggestion="全文检索并替换占位符，避免评审看出通用模板痕迹。",
        )
        for value in seen
    ]


def _check_checklist(rule: Rule, context: ComplianceContext) -> list[dict[str, Any]]:
    content = context.content_text
    findings = []
    for item in context.payload.get("submission_checklist", []) or []:
        text = str(item.get("item") or "")
        for token in _checklist_tokens(text):
            if token not in content:
                findings.append(
                    _finding(
                        rule,
                        message=f"材料清单项未在章节中响应：{token}",
                        location=item.get("source_span") or {"scope": "checklist"},
                        expected=token,
                        actual="未找到",
                        suggestion="补充对应章节或材料索引。",
                    )
                )
    return findings[:100]


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    match = re.search(r"(20\d{2})\s*[-年/]\s*(\d{1,2})\s*[-月/]\s*(\d{1,2})", value)
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def _check_deadline(rule: Rule, context: ComplianceContext) -> list[dict[str, Any]]:
    tender_deadline = None
    timeline_item = None
    for item in context.payload.get("timeline", []) or []:
        if "投标截止" in str(item.get("event") or ""):
            tender_deadline = _parse_date(str(item.get("at") or ""))
            timeline_item = item
            break
    if tender_deadline is None:
        return []
    project_deadline = (
        local_date(context.project.bid_deadline) if context.project.bid_deadline else None
    )
    if project_deadline is None:
        return [
            _finding(
                rule,
                message="项目未设置投标截止时间",
                location=timeline_item.get("source_span")
                if timeline_item
                else {"scope": "timeline"},
                expected=str(tender_deadline),
                actual="未设置",
                suggestion="在项目中录入投标截止时间。",
            )
        ]
    if project_deadline > tender_deadline:
        return [
            _finding(
                rule,
                message="项目投标截止时间晚于招标文件要求",
                location=timeline_item.get("source_span")
                if timeline_item
                else {"scope": "timeline"},
                expected=str(tender_deadline),
                actual=str(project_deadline),
                suggestion="按招标文件时间节点修正项目截止时间。",
            )
        ]
    return []


def _check_empty(rule: Rule, context: ComplianceContext) -> list[dict[str, Any]]:
    return [
        _finding(
            rule,
            message=f"章节内容为空：{section.title}",
            location={"section_id": str(section.id), "section_title": section.title},
            actual="空",
            suggestion="生成或补写该章节后再预检。",
        )
        for section in context.sections
        if not (section.content_text or "").strip()
    ]


def _check_asset_expiry(rule: Rule, context: ComplianceContext) -> list[dict[str, Any]]:
    if context.project.bid_deadline is None:
        return []
    deadline = local_date(context.project.bid_deadline)
    bid_validity_days = context.payload.get("bid_validity_days")
    if isinstance(bid_validity_days, int) and bid_validity_days > 0:
        coverage_until = deadline + timedelta(days=bid_validity_days)
        coverage_note = f"投标有效期 {bid_validity_days} 天"
    else:
        coverage_until = deadline
        coverage_note = "按投标截止日口径，投标有效期未解析"
    findings: list[dict[str, Any]] = []
    for kind, label in (("qualifications", "资质"), ("personnel", "人员")):
        for asset in context.assets.get(kind, []):
            valid_to = _parse_date(str(asset.get("valid_to") or ""))
            if valid_to is not None and valid_to < coverage_until:
                name = str(asset.get("name") or "")
                findings.append(
                    _finding(
                        rule,
                        message=f"{label}证书有效期早于覆盖截止日（{coverage_note}）：{name}",
                        location={"scope": kind, "asset_id": asset.get("id")},
                        expected=str(coverage_until),
                        actual=str(valid_to),
                        suggestion="更新证书或更换满足投标有效期的证明材料。",
                    )
                )
    return findings[:100]


def _check_asset_count(rule: Rule, context: ComplianceContext) -> list[dict[str, Any]]:
    kind_by_category = {
        "PERSONNEL": ("personnel", "人员"),
        "PERFORMANCE": ("performances", "业绩"),
        "QUALIFICATION": ("qualifications", "资质"),
    }
    findings: list[dict[str, Any]] = []
    for requirement in context.payload.get("qualification_requirements", []) or []:
        quantity = requirement.get("quantity")
        if not quantity:
            continue
        mapped = kind_by_category.get(str(requirement.get("category") or ""))
        if mapped is None:
            continue
        kind, label = mapped
        actual = len(context.assets.get(kind, []))
        if actual < int(quantity):
            findings.append(
                _finding(
                    rule,
                    message=f"{label}数量可能不足：要求 {quantity}，资产库 {actual}",
                    location=requirement.get("source_span") or {"scope": "assets"},
                    expected=str(quantity),
                    actual=str(actual),
                    suggestion=f"补充{label}材料后重新资格预审。",
                )
            )
    return findings[:100]


def _check_page_size(rule: Rule, context: ComplianceContext) -> list[dict[str, Any]]:
    format_text = " ".join(
        str((item.get("expect") or {}).get("text") or "")
        for item in context.payload.get("format_rules", [])
    )
    if "A4" not in format_text.upper():
        return []
    sections = list(getattr(context.docx_document, "sections", []))
    if not sections:
        return []
    page = sections[0]
    width = round(page.page_width.cm, 1) if page.page_width else None
    height = round(page.page_height.cm, 1) if page.page_height else None
    if width is None or height is None or abs(width - 21.0) > 0.3 or abs(height - 29.7) > 0.3:
        return [
            _finding(
                rule,
                message="docx 页面规格不是 A4",
                location={"scope": "docx_page"},
                expected="A4（21.0 × 29.7 cm）",
                actual=f"{width} × {height} cm" if width and height else "未设置",
                suggestion="按招标文件要求设置页面规格后重新导出。",
            )
        ]
    return []


def _check_format_assembly(rule: Rule, context: ComplianceContext) -> list[dict[str, Any]]:
    format_text = " ".join(
        str((item.get("expect") or {}).get("text") or "")
        for item in context.payload.get("format_rules", [])
    )
    if not any(keyword in format_text for keyword in ("封面", "目录", "页码")):
        return []
    final_assembly = getattr(context, "final_assembly", None) or {}
    if final_assembly.get("analyzed"):
        findings: list[dict[str, Any]] = []
        if "封面" in format_text and final_assembly.get("has_cover_name") is False:
            findings.append(
                _finding(
                    rule,
                    message="最终标书封面未检测到项目名称",
                    location={"scope": "final_document", "format": final_assembly.get("format")},
                    expected=str(getattr(context.project, "name", "") or ""),
                    actual="未找到",
                    suggestion="核对最终文件封面/投标函是否含项目名称；只提示，不提供任何元数据清理。",
                )
            )
        if "目录" in format_text and final_assembly.get("has_toc") is False:
            findings.append(
                _finding(
                    rule,
                    message="最终标书未检测到目录",
                    location={"scope": "final_document", "format": final_assembly.get("format")},
                    actual="未找到",
                    suggestion="补充目录并与正文页码对应后重新导出/上传。",
                )
            )
        if "页码" in format_text and final_assembly.get("has_page_numbers") is False:
            findings.append(
                _finding(
                    rule,
                    message="最终标书页脚未检测到页码",
                    location={"scope": "final_document", "format": final_assembly.get("format")},
                    actual="未找到",
                    suggestion="插入页码后重新导出；PDF 无法机械判定时请人工核对。",
                )
            )
        return findings[:10]
    document = context.docx_document
    doc_text = context.docx_text or context.content_text or ""
    # 同一函数内 findings 已在 if 分支声明过类型，这里不再重复标注（避免 no-redef）
    findings = []
    if "封面" in format_text:
        project_name = str(getattr(context.project, "name", "") or "")
        first_paragraphs = [
            paragraph.text.strip()
            for paragraph in getattr(document, "paragraphs", [])
            if paragraph.text.strip()
        ][:20]
        if project_name and not any(project_name in text for text in first_paragraphs):
            findings.append(
                _finding(
                    rule,
                    message="未在封面/前 20 段检测到项目名称",
                    location={"scope": "docx_cover"},
                    expected=project_name,
                    actual="未找到",
                    suggestion="按招标文件封面格式补项目名称/编号后重新导出，或人工核对。",
                )
            )
    if "目录" in format_text and "目录" not in doc_text:
        findings.append(
            _finding(
                rule,
                message="未检测到目录",
                location={"scope": "docx_toc"},
                actual="未找到",
                suggestion="补充目录并与正文页码对应；如线下制作请转人工项。",
            )
        )
    if "页码" in format_text:
        has_page_field = False
        for section in getattr(document, "sections", []) or []:
            try:
                if "PAGE" in section.footer._element.xml:
                    has_page_field = True
                    break
            except AttributeError:
                continue
        if not has_page_field:
            findings.append(
                _finding(
                    rule,
                    message="页脚未检测到页码域",
                    location={"scope": "docx_footer"},
                    actual="未找到 PAGE 域",
                    suggestion="在页脚插入页码后重新导出，或人工核对最终 PDF。",
                )
            )
    return findings[:10]


def _check_project_info(rule: Rule, context: ComplianceContext) -> list[dict[str, Any]]:
    cover = next(
        (
            section
            for section in context.sections
            if section.section_type == "cover" or "投标函" in section.title
        ),
        None,
    )
    if cover is None:
        return [
            _finding(
                rule,
                message="缺少投标函/封面章节",
                location={"scope": "sections"},
                actual="未找到",
                suggestion="补充投标函章节并包含项目名称/编号。",
            )
        ]
    text = cover.content_text or ""
    expected = [value for value in (context.project.name, context.project.code) if value]
    if expected and not any(str(value) in text for value in expected):
        return [
            _finding(
                rule,
                message="投标函未出现项目名称/编号",
                location={"section_id": str(cover.id), "section_title": cover.title},
                expected="、".join(str(value) for value in expected),
                actual="未找到",
                suggestion="在投标函中写明项目名称与编号，保持与招标文件一致。",
            )
        ]
    return []


def _check_project_code(rule: Rule, context: ComplianceContext) -> list[dict[str, Any]]:
    tender_code = str((context.payload.get("project") or {}).get("code") or "").strip()
    project_code = str(context.project.code or "").strip()
    if not tender_code or not project_code:
        return []
    if tender_code.lower() != project_code.lower():
        return [
            _finding(
                rule,
                message="项目编号与招标文件不一致",
                location={"scope": "project"},
                expected=tender_code,
                actual=project_code,
                suggestion="核对项目编号并修正项目主数据。",
            )
        ]
    return []


def _parse_amount(value: str | None) -> float | None:
    if not value:
        return None
    matched = re.search(r"-?\d+(?:\.\d+)?", str(value).replace(",", "").replace("，", ""))
    return float(matched.group(0)) if matched else None


def _extract_bid_price(context: ComplianceContext) -> float | None:
    cover_texts = [
        section.content_text
        for section in context.sections
        if section.section_type == "cover" or "投标函" in section.title
    ]
    texts = cover_texts or [section.content_text for section in context.sections]
    pattern = re.compile(
        r"(?:投标总价|投标报价|总报价|报价总额|投标价格)[^0-9]{0,16}"
        r"(\d[\d,，]*(?:\.\d+)?)\s*(万元|元)"
    )
    for text in texts:
        match = pattern.search(text or "")
        if not match:
            continue
        amount = _parse_amount(match.group(1))
        if amount is None:
            continue
        return amount * 10000 if match.group(2) == "万元" else amount
    return None


def _budget_amount(context: ComplianceContext) -> float | None:
    budget = context.payload.get("budget") or {}
    if isinstance(budget, dict):
        return _parse_amount(str(budget.get("amount"))) if budget.get("amount") else None
    return _parse_amount(str(budget))


def _check_price_over_budget(rule: Rule, context: ComplianceContext) -> list[dict[str, Any]]:
    bid = _extract_bid_price(context)
    budget = _budget_amount(context)
    if bid is None or budget is None or bid <= budget:
        return []
    return [
        _finding(
            rule,
            message="投标报价高于招标预算",
            location={"scope": "bid_letter"},
            expected=f"{budget:.2f}",
            actual=f"{bid:.2f}",
            suggestion="复核报价构成，确保不超过预算或按招标文件规则处理。",
        )
    ]


def _check_price_low(rule: Rule, context: ComplianceContext) -> list[dict[str, Any]]:
    bid = _extract_bid_price(context)
    budget = _budget_amount(context)
    if bid is None or budget is None or bid >= budget * 0.7:
        return []
    return [
        _finding(
            rule,
            message="投标报价低于预算的 70%，可能存在异常低价风险",
            location={"scope": "bid_letter"},
            expected=f"≥ {budget * 0.7:.2f}",
            actual=f"{bid:.2f}",
            suggestion="核查成本构成与低价风险，必要时补充说明。",
        )
    ]



def _shingles(text: str, size: int = 3) -> frozenset[str]:
    normalized = re.sub(r"\s+", "", text)
    return frozenset(normalized[i : i + size] for i in range(max(0, len(normalized) - size + 1)))


def _check_kb_duplicate(rule: Rule, context: ComplianceContext) -> list[dict[str, Any]]:
    """6.1 跨库查重：章节段落 vs 知识库文本块 3-gram 包容率（启发式粗测）。

    阈值经租户策略 `threshold_override` 可调，默认 0.6。
    """
    if not context.kb_texts:
        return []
    threshold = rule.threshold if rule.threshold is not None else 0.6
    chunk_shingles = [(text, _shingles(text)) for text in context.kb_texts[:300]]
    findings: list[dict[str, Any]] = []
    for section in context.sections:
        for line in (section.content_text or "").splitlines():
            text = line.strip()
            if len(re.sub(r"\s+", "", text)) < 30:
                continue
            line_shingles = _shingles(text)
            if not line_shingles:
                continue
            best_ratio = 0.0
            for _chunk_text, chunk in chunk_shingles:
                if not chunk:
                    continue
                ratio = len(line_shingles & chunk) / len(line_shingles)
                best_ratio = max(best_ratio, ratio)
            if best_ratio >= threshold:
                findings.append(
                    _finding(
                        rule,
                        message=f"章节段落与知识库内容高度雷同（相似度 {best_ratio:.0%}），建议改写",
                        location={"sections": [{"id": str(section.id), "title": section.title}]},
                        actual=text[:160],
                        suggestion="结合本项目评审点改写，避免大段复用历史标书材料。",
                    )
                )
                if len(findings) >= 20:
                    return findings
    return findings


def _check_duplicate_paragraphs(rule: Rule, context: ComplianceContext) -> list[dict[str, Any]]:
    groups: dict[str, list[tuple[Any, str]]] = {}
    for section in context.sections:
        for line in (section.content_text or "").splitlines():
            text = line.strip()
            if len(text) < 40:
                continue
            normalized = re.sub(r"[\s，。；;：:、（）()“”\"'']", "", text)
            if len(normalized) < 30:
                continue
            groups.setdefault(normalized, []).append((section, text))
    findings: list[dict[str, Any]] = []
    for occurrences in groups.values():
        section_ids = {str(section.id) for section, _ in occurrences}
        if len(section_ids) < 2:
            continue
        section, text = occurrences[0]
        titles = [item[0].title for item in occurrences]
        findings.append(
            _finding(
                rule,
                message=f"多个章节存在相同段落：{'、'.join(dict.fromkeys(titles))}",
                location={
                    "sections": [
                        {"id": str(item[0].id), "title": item[0].title} for item in occurrences
                    ]
                },
                actual=text[:160],
                suggestion="按各章节评审点改写，避免复制粘贴造成重复率风险。",
            )
        )
    return findings[:100]


HANDLERS = {
    "CONSIST-NUM-001": lambda rule, context: check_consistency_numbers(rule, context, _finding),
    "QUOTE-ARITH-001": lambda rule, context: check_quote_arithmetic(rule, context, _finding),
    "DEPOSIT-LIMIT-001": lambda rule, context: check_deposit_limit(rule, context, _finding),
    "WORKLOAD-COST-001": lambda rule, context: check_workload_cost(rule, context, _finding),
    "CONSIST-PKG-001": lambda rule, context: check_package_selection(rule, context, _finding),
    "RESP-OPEN-001": lambda rule, context: check_open_responses(rule, context, _finding),
    "RESP-LINK-001": lambda rule, context: check_unlinked_responses(rule, context, _finding),
    "DARK-NAME-001": _check_dark_names,
    "DARK-META-001": _check_dark_meta,
    "DARK-IMG-001": _check_dark_images,
    "FORMAT-FONT-001": _check_format_font,
    "FORMAT-SIZE-001": _check_format_size,
    "QUAL-FAIL-001": lambda rule, context: _check_qualification(rule, context, "FAIL"),
    "QUAL-UNKNOWN-001": lambda rule, context: _check_qualification(rule, context, "UNKNOWN"),
    "EVID-UNSOURCED-001": _check_unsourced,
    "CHECKLIST-MISSING-001": _check_checklist,
    "CONTENT-PLACEHOLDER-001": _check_placeholder,
    "TIME-DEADLINE-001": _check_deadline,
    "CONTENT-EMPTY-001": _check_empty,
    "QUAL-EXPIRY-001": _check_asset_expiry,
    "QUAL-COUNT-001": _check_asset_count,
    "FORMAT-PAGE-001": _check_page_size,
    "FORMAT-ASSEMBLY-001": _check_format_assembly,
    "CONTENT-PROJECT-001": _check_project_info,
    "CONSISTENCY-CODE-001": _check_project_code,
    "SIMILARITY-DUP-001": _check_duplicate_paragraphs,
    "SIMILARITY-KB-001": _check_kb_duplicate,
    "PRICE-OVER-BUDGET-001": _check_price_over_budget,
    "PRICE-ABNORMAL-LOW-001": _check_price_low,
}


def evaluate(rules: list[Rule], context: ComplianceContext) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for rule in rules:
        handler = HANDLERS.get(rule.rule_id)
        if handler is None:
            raise ValueError(f"no handler for compliance rule {rule.rule_id}")
        findings.extend(handler(rule, context))
    return findings
