from datetime import UTC, datetime
from types import SimpleNamespace

from docx import Document
from docx.shared import Cm

from app.modules.compliance.consistency import (
    check_deposit_limit,
    check_quote_arithmetic,
)
from app.modules.compliance.engine import (
    HANDLERS,
    _check_asset_count,
    _check_asset_expiry,
    _check_dark_images,
    _check_dark_meta,
    _check_duplicate_paragraphs,
    _check_format_assembly,
    _check_page_size,
    _check_placeholder,
    _check_price_low,
    _check_price_over_budget,
    _check_project_code,
    _check_project_info,
    _finding,
)
from app.modules.compliance.registry import Rule, load_rule_set


def _rule(rule_id: str) -> Rule:
    _, rules = load_rule_set()
    return next(rule for rule in rules if rule.rule_id == rule_id)


def _context(**overrides):
    values = {
        "project": SimpleNamespace(
            name="政务云项目", code="P-001", bid_deadline=datetime(2026, 11, 10, tzinfo=UTC)
        ),
        "payload": {},
        "sections": [],
        "assets": {},
        "qualification_results": [],
        "unsourced_claims": [],
        "docx_document": Document(),
        "docx_text": "",
        "content_text": "",
        "company_name": None,
        "tenant_name": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_all_rules_have_handlers() -> None:
    version, rules = load_rule_set()

    assert version
    assert len(rules) >= 18
    assert {rule.rule_id for rule in rules} == set(HANDLERS)
    assert all(rule.type in {"HARD", "SOFT"} for rule in rules)


def test_expired_assets_and_insufficient_counts() -> None:
    context = _context(
        assets={
            "qualifications": [
                {"id": "a1", "name": "系统集成资质", "valid_to": "2026-01-01"},
                {"id": "a2", "name": "安全资质", "valid_to": "2027-01-01"},
            ],
            "personnel": [],
            "performances": [],
            "credits": [],
        },
        payload={
            "qualification_requirements": [
                {"category": "PERSONNEL", "quantity": 2, "source_span": {"quote": "人员不少于2名"}}
            ]
        },
    )

    expiry = _check_asset_expiry(_rule("QUAL-EXPIRY-001"), context)
    count = _check_asset_count(_rule("QUAL-COUNT-001"), context)

    assert len(expiry) == 1
    assert expiry[0]["actual"] == "2026-01-01"
    assert len(count) == 1
    assert count[0]["expected"] == "2"


def test_quote_arithmetic_and_deposit_limits() -> None:
    document = Document()
    table = document.add_table(rows=2, cols=3)
    table.rows[0].cells[0].text = "单价"
    table.rows[0].cells[1].text = "数量"
    table.rows[0].cells[2].text = "合价"
    table.rows[1].cells[0].text = "100"
    table.rows[1].cells[1].text = "3"
    table.rows[1].cells[2].text = "400"

    context = _context(
        content_text=(
            "投标总价：500万元\n"
            "投标报价：480万元\n"
            "大写：人民币伍佰万元整 小写：¥4,800,000元\n"
        ),
        docx_document=document,
        payload={
            "budget": {"amount": "5000000", "label": "最高限价"},
            "deposit": {"amount": "120000", "validity_days": 60},
            "bid_validity_days": 90,
        },
    )

    quote = check_quote_arithmetic(_rule("QUOTE-ARITH-001"), context, _finding)
    deposit = check_deposit_limit(_rule("DEPOSIT-LIMIT-001"), context, _finding)

    assert any("多个不同金额" in item["message"] for item in quote)
    assert any("大写与小写" in item["message"] for item in quote)
    assert any("单价×数量" in item["message"] for item in quote)
    assert any("超过估算价" in item["message"] for item in deposit)
    assert any("有效期短于" in item["message"] for item in deposit)


def test_expiry_uses_bid_validity_days() -> None:
    context = _context(
        assets={
            "qualifications": [
                {"id": "a1", "name": "系统集成资质", "valid_to": "2026-12-01"}
            ],
            "personnel": [],
            "performances": [],
            "credits": [],
        },
        payload={"bid_validity_days": 90},
    )

    findings = _check_asset_expiry(_rule("QUAL-EXPIRY-001"), context)
    assert len(findings) == 1
    assert "投标有效期 90 天" in findings[0]["message"]
    assert findings[0]["expected"] == "2027-02-08"


def test_placeholder_rule_flags_template_traces() -> None:
    context = _context(content_text="本项目由某单位建设，XX 系统待填。")
    findings = _check_placeholder(_rule("CONTENT-PLACEHOLDER-001"), context)
    values = {item["actual"] for item in findings}
    assert {"某单位", "XX", "待填"} <= values


def test_format_assembly_flags_missing_cover_toc_and_page_field() -> None:
    context = _context(
        payload={
            "format_rules": [
                {"expect": {"text": "投标文件应有封面、目录和页码"}}
            ]
        }
    )
    findings = _check_format_assembly(_rule("FORMAT-ASSEMBLY-001"), context)
    messages = " ".join(item["message"] for item in findings)
    assert "封面" in messages
    assert "目录" in messages
    assert "页码" in messages


def test_page_size_and_project_consistency() -> None:
    context = _context(
        payload={
            "format_rules": [{"expect": {"text": "投标文件采用A4纸"}}],
            "project": {"code": "P-999"},
        },
        sections=[
            SimpleNamespace(
                id="s1", title="投标函", section_type="cover", content_text="致：某采购人"
            )
        ],
    )

    page = _check_page_size(_rule("FORMAT-PAGE-001"), context)
    info = _check_project_info(_rule("CONTENT-PROJECT-001"), context)
    code = _check_project_code(_rule("CONSISTENCY-CODE-001"), context)

    assert len(page) == 1
    assert len(info) == 1
    assert len(code) == 1

    context.docx_document.sections[0].page_width = Cm(21.0)
    context.docx_document.sections[0].page_height = Cm(29.7)
    assert _check_page_size(_rule("FORMAT-PAGE-001"), context) == []
    context.sections[0].content_text = "政务云项目 P-001 投标函"
    assert _check_project_info(_rule("CONTENT-PROJECT-001"), context) == []


def test_price_anomaly_and_duplicate_paragraphs() -> None:
    cover = SimpleNamespace(
        id="c1",
        title="投标函",
        section_type="cover",
        content_text="投标报价：1200000元",
    )
    technical = SimpleNamespace(
        id="t1",
        title="技术方案",
        section_type="technical",
        content_text="本方案采用双活架构，提供实施计划、人员安排与保障措施，确保业务连续运行，并按照招标文件要求逐项响应评审标准。",
    )
    service = SimpleNamespace(
        id="s1",
        title="服务承诺",
        section_type="service",
        content_text="本方案采用双活架构，提供实施计划、人员安排与保障措施，确保业务连续运行，并按照招标文件要求逐项响应评审标准。",
    )
    context = _context(
        sections=[cover, technical, service],
        payload={"budget": {"amount": "1000000"}},
    )

    over = _check_price_over_budget(_rule("PRICE-OVER-BUDGET-001"), context)
    low = _check_price_low(_rule("PRICE-ABNORMAL-LOW-001"), context)
    duplicates = _check_duplicate_paragraphs(_rule("SIMILARITY-DUP-001"), context)

    assert len(over) == 1
    assert low == []
    assert len(duplicates) == 1

    cover.content_text = "投标报价：600000元"
    assert _check_price_over_budget(_rule("PRICE-OVER-BUDGET-001"), context) == []
    assert len(_check_price_low(_rule("PRICE-ABNORMAL-LOW-001"), context)) == 1


def test_dark_meta_only_when_dark_bid_required() -> None:
    rule = _rule("DARK-META-001")
    context = _context(payload={"dark_bid": {"required": False}})
    context.docx_document.core_properties.author = "张三"
    assert _check_dark_meta(rule, context) == []

    context.payload = {"dark_bid": {"required": True}}
    findings = _check_dark_meta(rule, context)
    assert len(findings) == 1
    assert "author" in findings[0]["actual"]


def test_dark_image_rule_flags_sensitive_identity_and_missing() -> None:
    rule = _rule("DARK-IMG-001")
    section = SimpleNamespace(
        id="s1",
        title="技术方案",
        content_json=[
            {"type": "image", "image_id": "img-sensitive"},
            {"type": "image", "image_id": "img-identity"},
            {"type": "image", "image_id": "img-safe"},
            {"type": "image", "image_id": "img-missing"},
        ],
    )
    images = {
        "img-sensitive": SimpleNamespace(
            id="img-sensitive", title="资质证书", caption=None, filename="cert.png",
            tags=[], sensitive=True,
        ),
        "img-identity": SimpleNamespace(
            id="img-identity", title="演示投标企业产品图", caption=None, filename="logo.png",
            tags=[], sensitive=False,
        ),
        "img-safe": SimpleNamespace(
            id="img-safe", title="架构图", caption=None, filename="arch.png",
            tags=[], sensitive=False,
        ),
    }
    context = _context(
        payload={"dark_bid": {"required": True}},
        sections=[section],
        knowledge_images=images,
        company_name="演示投标企业",
        tenant_name="演示投标企业",
    )
    findings = _check_dark_images(rule, context)
    messages = " ".join(finding["message"] for finding in findings)
    assert len(findings) == 3
    assert "敏感" in messages
    assert "企业/人员名" in messages
    assert "已不存在" in messages

    context.payload = {"dark_bid": {"required": False}}
    assert _check_dark_images(rule, context) == []
