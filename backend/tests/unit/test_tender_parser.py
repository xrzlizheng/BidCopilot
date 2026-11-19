from datetime import datetime
from io import BytesIO

import pytest
from docx import Document
from pypdf import PdfWriter

from app.modules.tender.parser import (
    CST,
    ScannedDocumentError,
    UnsupportedDocumentError,
    _extract_timeline,
    build_constraint_set,
    parse_document,
    parsed_bid_deadline,
    resolve_relative_timeline,
)
from tests.support import sample_tender_docx, sample_tender_docx_merged_scoring


def _docx_with_paragraph(text: str) -> bytes:
    document = Document()
    document.add_paragraph(text)
    document.add_paragraph("投标截止时间为 2026年11月10日 09:00。")
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _docx_with_scoring_table(header_row: int) -> bytes:
    document = Document()
    document.add_paragraph("第三章 评标办法")
    table = document.add_table(rows=header_row + 3, cols=3)
    for index in range(header_row):
        table.cell(index, 0).text = f"编制说明 {index}"
    table.cell(header_row, 0).text = "评分项"
    table.cell(header_row, 1).text = "分值"
    table.cell(header_row, 2).text = "评分标准"
    for offset, (name, score) in enumerate((("技术方案", "40 分"), ("投标报价", "30 分"))):
        table.cell(header_row + 1 + offset, 0).text = name
        table.cell(header_row + 1 + offset, 1).text = score
        table.cell(header_row + 1 + offset, 2).text = "完整可行"
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def test_docx_constraint_extraction() -> None:
    parsed = parse_document(sample_tender_docx(), "sample.docx")
    constraints = build_constraint_set(parsed, {"name": "测试项目"})

    assert len(constraints["scoring_items"]) >= 3
    scores = {item["item"]: item["score"] for item in constraints["scoring_items"]}
    assert scores["技术方案"] == 40.0
    assert scores["项目经理（PMP）"] == 20.0
    assert scores["投标报价"] == 40.0
    assert constraints["scoring_items"][0]["source_span"]["table_index"] == 0

    assert constraints["reject_clauses"]
    assert "否决" in constraints["reject_clauses"][0]["text"]

    categories = {item["category"] for item in constraints["qualification_requirements"]}
    assert {"QUALIFICATION", "PERSONNEL", "PERFORMANCE"} <= categories

    assert constraints["budget"]["amount"] == "1200000"
    assert constraints["timeline"][0]["event"] == "投标截止"
    assert constraints["dark_bid"]["required"] is True
    assert constraints["submission_checklist"]
    assert constraints["format_rules"]
    assert constraints["parse_meta"]["parser_version"] == "rules-v1.3"


def test_bid_validity_deposit_front_appendix_and_star_clauses() -> None:
    text = """
    某项目招标文件
    最高限价：人民币 500 万元。
    投标有效期：90 天。
    投标保证金：人民币 10 万元，投标保证金有效期 60 天。
    投标人须知前附表
    投标文件应密封并加盖公章。
    ★ 系统可用性不低于 99.95%。
    ▲ 项目经理须具备 PMP 证书。
    """
    parsed = parse_document(text.encode("utf-8"), "tender.txt")
    constraints = build_constraint_set(parsed, {})

    assert constraints["budget"]["label"] == "最高限价"
    assert constraints["bid_validity_days"] == 90
    assert constraints["deposit"]["amount"] == "100000"
    assert constraints["deposit"]["validity_days"] == 60
    assert constraints["front_appendix"]
    assert any("密封" in item["text"] for item in constraints["front_appendix"])
    markers = {item["marker"] for item in constraints["star_clauses"]}
    assert {"★", "▲"} <= markers
    assert any("99.95%" in item["text"] for item in constraints["technical_parameters"])


def test_unsupported_document_type() -> None:
    with pytest.raises(UnsupportedDocumentError):
        parse_document(b"data", "sample.exe")


def test_scanned_pdf_is_rejected() -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    buffer = BytesIO()
    writer.write(buffer)

    with pytest.raises(ScannedDocumentError):
        parse_document(buffer.getvalue(), "scan.pdf")


def _timeline(text: str) -> list[dict]:
    parsed = parse_document(text.encode("utf-8"), "tender.txt")
    return build_constraint_set(parsed, {})["timeline"]


def test_timeline_captures_every_date_in_a_block() -> None:
    events = _timeline("获取招标文件：2026年12月20日至2026年12月30日。")
    assert [entry["at"] for entry in events] == ["2026年12月20日", "2026年12月30日"]


def test_bid_deadline_requires_explicit_clock_time() -> None:
    assert parsed_bid_deadline(_timeline("投标截止时间为 2027年1月8日 10:00。")) == datetime(
        2027, 1, 8, 10, 0, tzinfo=CST
    )
    assert parsed_bid_deadline(_timeline("投标截止时间为 2027年1月8日。")) is None
    assert parsed_bid_deadline(_timeline("本项目预算 100 万元。")) is None


def test_bid_deadline_ignores_non_deadline_events() -> None:
    events = _timeline("开标时间：2027年1月8日 10:30。\n投标截止时间：2027年1月9日 09:00。")
    deadline = parsed_bid_deadline(events)
    assert deadline == datetime(2027, 1, 9, 9, 0, tzinfo=CST)
    assert parsed_bid_deadline([e for e in events if e["event"] != "投标截止"]) is None


def test_clarification_items_extracted_from_change_document() -> None:
    text = (
        "某项目澄清通知\n"
        "各投标人：现对招标文件作如下澄清：\n"
        "1. 技术方案评分由 40 分调整为 45 分。\n"
        "2. 投标截止时间修改为 2027年3月10日 14:30。\n"
        "3. 关于资质问题答复：具备乙级即可。\n"
    )
    parsed = parse_document(text.encode("utf-8"), "clarify.txt")
    clarifications = build_constraint_set(parsed, {})["clarifications"]
    assert len(clarifications) == 3
    assert any("45" in item["text"] for item in clarifications)


def test_plain_tender_has_no_clarifications() -> None:
    parsed = parse_document("某招标文件\n投标人须具备营业执照。\n".encode(), "plain.txt")
    assert build_constraint_set(parsed, {})["clarifications"] == []


def test_scoring_fallback_ignores_change_phrasing() -> None:
    text = "某项目澄清通知\n技术方案评分由 40 分调整为 45 分。\n"
    parsed = parse_document(text.encode("utf-8"), "clarify.txt")
    assert build_constraint_set(parsed, {})["scoring_items"] == []


def test_scoring_fallback_strips_list_prefix() -> None:
    text = "某项目\n1. 技术方案：40 分\n2. 投标报价：60 分\n"
    parsed = parse_document(text.encode("utf-8"), "list.txt")
    items = build_constraint_set(parsed, {})["scoring_items"]
    assert [(item["item"], item["score"]) for item in items] == [
        ("技术方案", 40.0),
        ("投标报价", 60.0),
    ]


def test_budget_prefers_labelled_amount_over_policy_sentence() -> None:
    """「预算金额未达100万元的采购项目…」是说明句，不能当成预算金额。"""
    parsed = parse_document(sample_tender_docx_merged_scoring(), "merged.docx")
    constraints = build_constraint_set(parsed, {"name": "测试项目"})
    assert constraints["budget"]["amount"] == "1559100"
    assert "1559100" in constraints["budget"]["source_span"]["quote"]


def test_scoring_table_splits_rows_sharing_one_item_cell() -> None:
    """合并单元格让多行共用「评审内容」，应按评审细则拆成多条而不是只留一条。"""
    parsed = parse_document(sample_tender_docx_merged_scoring(), "merged.docx")
    constraints = build_constraint_set(parsed, {"name": "测试项目"})
    items = constraints["scoring_items"]

    assert len(items) == 4
    assert [item["score"] for item in items] == [2.0, 2.0, 18.0, 5.0]
    names = [item["item"] for item in items]
    assert "投标供应商履约能力评价｜1.1质量管理能力评价" in names
    assert "投标供应商履约能力评价｜1.2信息安全管理能力评价" in names
    assert "投标响应情况" in names
    assert sum(item["score"] for item in items) == 27.0


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("预算金额：100万元（不含税）", "1000000"),
        ("投标限价：150万元（含税）", "1500000"),
        ("最高限价：200万元以下", "2000000"),
        ("预算金额（元）：1559100", "1559100"),
        ("本项目预算金额为人民币 1,559,100 元。", "1559100"),
    ],
)
def test_budget_accepts_common_qualifiers(text: str, expected: str) -> None:
    """「含税/不含税/以下/大写」都是有效预算，不能被排除词误伤。"""
    parsed = parse_document(_docx_with_paragraph(text), "q.docx")
    assert build_constraint_set(parsed, {"name": "测试项目"})["budget"]["amount"] == expected


@pytest.mark.parametrize(
    "text",
    [
        "预算金额未达100万元的采购项目，由采购人处理采购争议。",
        "预算金额不超过300万元的，按小额采购办理。",
    ],
)
def test_budget_rejects_threshold_sentences(text: str) -> None:
    parsed = parse_document(_docx_with_paragraph(text), "q.docx")
    assert build_constraint_set(parsed, {"name": "测试项目"})["budget"]["amount"] is None


@pytest.mark.parametrize("header_row", [0, 3, 5, 8, 12])
def test_scoring_header_detected_at_any_depth(header_row: int) -> None:
    """真实公告常在表头前放标题行/编制说明行，表头位置不能写死在前 5 行。"""
    parsed = parse_document(_docx_with_scoring_table(header_row), "deep.docx")
    items = build_constraint_set(parsed, {"name": "测试项目"})["scoring_items"]
    assert [item["item"] for item in items] == ["技术方案", "投标报价"]


@pytest.mark.parametrize(
    ("text", "expected_iso"),
    [
        ("投标截止时间为 2025年9月19日 09:00。", "2025-09-19T09:00:00+08:00"),
        ("投标截止时间：2025年9月19日上午9时30分。", "2025-09-19T09:30:00+08:00"),
        ("开标时间：2025年9月19日下午3点整", "2025-09-19T15:00:00+08:00"),
        ("开标时间：2025年9月19日下午3点30分", "2025-09-19T15:30:00+08:00"),
        ("投标截止时间 2025-09-19 9:05", "2025-09-19T09:05:00+08:00"),
    ],
)
def test_deadline_supports_chinese_clock_forms(text: str, expected_iso: str) -> None:
    """「上午9时30分」「下午3点」是公告里的主流写法，识别不了就拿不到截止时间。"""
    timeline = _extract_timeline(parse_document(_docx_with_paragraph(text), "c.docx").blocks)
    stamps = [entry.get("deadline_at") for entry in timeline if entry.get("deadline_at")]
    assert expected_iso in stamps


@pytest.mark.parametrize(
    ("text", "days", "unit"),
    [
        ("投标文件递交截止时间：自公告发布之日起 5 个工作日。", 5, "工作日"),
        ("答疑截止时间为公告发布后 3 个日历日。", 3, "日历日"),
        ("报名时间自公告发布之日起 7 天。", 7, "天"),
        ("获取招标文件时间：公告发布后 2 周内。", 2, "周"),
    ],
)
def test_relative_deadline_is_kept_with_unit(text: str, days: int, unit: str) -> None:
    """相对表述抽不到绝对日期，但必须留痕，否则项目会「没有截止时间」。"""
    timeline = _extract_timeline(parse_document(_docx_with_paragraph(text), "r.docx").blocks)
    relatives = [entry for entry in timeline if entry.get("relative_days")]
    assert relatives, timeline
    assert relatives[0]["relative_days"] == days
    assert relatives[0]["relative_unit"] == unit
    assert relatives[0]["needs_confirmation"] is True


def test_absolute_date_is_not_mistaken_for_relative_days() -> None:
    """「2025年9月19日」里的「19 日」不能被当成相对天数。"""
    timeline = _extract_timeline(
        parse_document(_docx_with_paragraph("投标截止时间为 2025年9月19日 09:00。"), "a.docx").blocks
    )
    assert [entry for entry in timeline if entry.get("relative_days")] == []


def test_relative_deadline_resolved_from_publish_date() -> None:
    """5 个工作日 = 自然日跳过周末；2025-09-01 是周一，应得 09-08。"""
    from datetime import date

    timeline = _extract_timeline(
        parse_document(
            _docx_with_paragraph("投标文件递交截止时间：自公告发布之日起 5 个工作日。"), "r.docx"
        ).blocks
    )
    resolved, notes = resolve_relative_timeline(timeline, date(2025, 9, 1))
    entry = next(item for item in resolved if item.get("deadline_at"))
    assert entry["deadline_at"].startswith("2025-09-08")
    assert notes and "请核对" in notes[0]


def test_relative_deadline_without_anchor_stays_unresolved() -> None:
    """没有公告发布日就不猜——猜错会让用户按错误日期备标。"""
    timeline = _extract_timeline(
        parse_document(
            _docx_with_paragraph("投标文件递交截止时间：自公告发布之日起 5 个工作日。"), "r.docx"
        ).blocks
    )
    resolved, notes = resolve_relative_timeline(timeline, None)
    assert all(not item.get("deadline_at") for item in resolved if item.get("relative_days"))
    assert notes and "人工确认" in notes[0]


def test_workday_skips_weekend_and_holiday() -> None:
    """2026-10-01 起 3 个工作日要跳过国庆假期。"""
    from datetime import date

    from app.modules.tender.parser import _add_workdays

    assert _add_workdays(date(2026, 9, 30), 1) == date(2026, 10, 9)


def test_publish_date_found_from_body() -> None:
    from app.modules.tender.parser import find_publish_date

    blocks = parse_document(
        _docx_with_paragraph("公告发布时间：2025年9月1日"), "p.docx"
    ).blocks
    assert find_publish_date(blocks) is not None
    assert find_publish_date(blocks).isoformat() == "2025-09-01"
