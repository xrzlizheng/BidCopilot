from types import SimpleNamespace

from app.modules.compliance.consistency import check_package_selection
from app.modules.compliance.engine import _finding
from app.modules.compliance.registry import Rule
from app.modules.tender.parser import build_constraint_set, parse_document

RULE = Rule(
    rule_id="CONSIST-PKG-001",
    name="多标包未指定投标包件",
    category="consistency",
    type="SOFT",
    severity="medium",
    expect="多标包需指定包件",
    basis="按包评审",
    auto_fixable=False,
    needs_review=True,
)


def _packages(text: str) -> dict:
    parsed = parse_document(text.encode("utf-8"), "pkg.txt")
    return build_constraint_set(parsed, {})["packages"]


def test_package_declaration_and_items_are_extracted() -> None:
    text = (
        "某项目招标文件\n本项目划分为 3 个标包。\n"
        "标包一：网络设备采购\n标包二：机房改造\n包3：运维服务\n"
    )
    packages = _packages(text)
    assert packages["declared_count"] == 3
    assert [item["code"] for item in packages["items"]] == ["一", "二", "3"]
    assert packages["items"][0]["name"] == "网络设备采购"


def test_single_package_tender_reports_nothing() -> None:
    packages = _packages("普通招标文件\n投标人须具备营业执照。\n")
    assert packages["declared_count"] is None
    assert packages["items"] == []


def _context(package_code: str | None, declared: int | None, items: list[dict]) -> SimpleNamespace:
    return SimpleNamespace(
        payload={"packages": {"declared_count": declared, "items": items}},
        project=SimpleNamespace(package_code=package_code),
    )


def test_rule_flags_missing_package_selection() -> None:
    context = _context(None, 3, [{"code": "一", "name": "网络设备"}])
    findings = check_package_selection(RULE, context, _finding)
    assert len(findings) == 1
    assert findings[0]["rule_id"] == "CONSIST-PKG-001"


def test_rule_passes_when_package_selected() -> None:
    context = _context("一", 3, [{"code": "一", "name": "网络设备"}])
    assert check_package_selection(RULE, context, _finding) == []


def test_rule_ignores_single_package_tenders() -> None:
    assert check_package_selection(RULE, _context(None, None, []), _finding) == []
    assert check_package_selection(RULE, _context(None, 1, []), _finding) == []
