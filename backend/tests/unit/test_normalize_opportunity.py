import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from app.core.html import sanitize_html
from app.modules.gateway.normalize import (
    looks_like_object_ref,
    normalize_opportunity,
    normalize_page,
    parse_amount,
    parse_datetime,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "keeprisk"


def test_normalize_real_list_fixture() -> None:
    payload = json.loads((FIXTURES / "e_007_1271_list.json").read_text(encoding="utf-8"))
    fetched_at = datetime.now(UTC)

    items, warnings = normalize_page(payload["data"], fetched_at=fetched_at)

    assert len(items) == 3
    assert warnings == []
    first = items[0]
    assert first.source_id == "526498d2fcc11acb5c80781a3128256a"
    assert first.title.startswith("【企业采购】")
    assert first.source == "keeprisk:E_007_1271"
    assert first.detail_status == "list_only"
    assert first.info_type == "tender"
    assert "四川" in (first.region or "")


def test_normalize_detail_fixture_with_content_and_award() -> None:
    payload = json.loads((FIXTURES / "e_007_1271_detail.json").read_text(encoding="utf-8"))
    fetched_at = datetime.now(UTC)

    items, warnings = normalize_page(payload["data"], fetched_at=fetched_at, include_content=True)

    assert len(items) == 2
    assert warnings == []
    award = next(item for item in items if item.info_type == "award")
    assert award.detail_status == "full"
    assert award.content_html
    assert award.awards
    assert award.awards[0].winner == "中国移动通信集团辽宁有限公司盘锦分公司"
    assert award.awards[0].win_amount == Decimal("1298520.0")


def test_missing_title_becomes_warning() -> None:
    item, warnings = normalize_opportunity({"uid": "u1"}, fetched_at=datetime.now(UTC))

    assert item is None
    assert warnings[0].code == "missing_title"


def test_amount_takes_max_value() -> None:
    assert parse_amount("1298520.0000 300.00") == Decimal("1298520.0000")
    assert parse_amount("1,200.50") == Decimal("1200.50")
    assert parse_amount("") is None


def test_datetime_formats() -> None:
    assert parse_datetime("2026-10-16 00:00:00") == datetime(2026, 10, 16, tzinfo=UTC)
    assert parse_datetime("2026-10-16") == datetime(2026, 10, 16, tzinfo=UTC)


def test_html_sanitizer_blocks_scripts_and_javascript_urls() -> None:
    cleaned, text = sanitize_html(
        '<script>alert(1)</script><p onclick="x">公告</p><a href="javascript:alert(1)">链接</a>'
    )

    assert "<script" not in cleaned
    assert "onclick" not in cleaned
    assert "javascript:" not in cleaned
    assert "公告" in text


def _raw(info_type: str, **overrides):
    item = {
        "title": "某市政务云运维项目",
        "bidEntInfos": "某市大数据局",
        "publishDate": "2026-10-16 00:00:00",
        "projectNo": "P-001",
        "infoType": info_type,
        "winEidInfos": "某集成商",
    }
    item.update(overrides)
    return item


def test_missing_uid_warns_and_keys_by_type() -> None:
    tender, tender_warnings = normalize_opportunity(_raw("1"), fetched_at=datetime.now(UTC))
    award, award_warnings = normalize_opportunity(_raw("3"), fetched_at=datetime.now(UTC))

    assert tender is not None and award is not None
    assert [item.code for item in tender_warnings].count("missing_uid") == 1
    assert [item.code for item in award_warnings].count("missing_uid") == 1
    # 同标题/采购人/发布时间，不同信息类型必须得到不同身份，不能互相覆盖。
    assert tender.dedup_key != award.dedup_key
    assert {tender.info_type, award.info_type} == {"tender", "award"}


def test_missing_uid_same_type_is_stable_for_dedup() -> None:
    first, _ = normalize_opportunity(_raw("1"), fetched_at=datetime.now(UTC))
    second, _ = normalize_opportunity(_raw("1"), fetched_at=datetime.now(UTC))
    assert first is not None and second is not None
    assert first.dedup_key == second.dedup_key


def test_missing_uid_fallback_canonicalizes_timezone() -> None:
    utc, _ = normalize_opportunity(
        _raw("1", publishDate="2026-10-16T00:00:00+00:00"), fetched_at=datetime.now(UTC)
    )
    cst, _ = normalize_opportunity(
        _raw("1", publishDate="2026-10-16T08:00:00+08:00"), fetched_at=datetime.now(UTC)
    )
    assert utc is not None and cst is not None
    assert utc.dedup_key == cst.dedup_key


def test_uid_path_keeps_identity_and_skips_warning() -> None:
    item, warnings = normalize_opportunity(
        _raw("1", uid="526498d2fcc11acb5c80781a3128256a"), fetched_at=datetime.now(UTC)
    )
    assert item is not None
    assert item.source_id == "526498d2fcc11acb5c80781a3128256a"
    assert item.dedup_key == item.source_id
    assert all(warning.code != "missing_uid" for warning in warnings)


def test_missing_uid_award_fallback_uses_title() -> None:
    first, _ = normalize_opportunity(
        _raw("3", title="项目甲中标公告"), fetched_at=datetime.now(UTC)
    )
    second, _ = normalize_opportunity(
        _raw("3", title="项目乙中标公告"), fetched_at=datetime.now(UTC)
    )
    assert first is not None and second is not None
    assert first.awards[0].dedup_key != second.awards[0].dedup_key


def test_looks_like_object_ref_detects_storage_key_only() -> None:
    # 实测线上出现的形状：bid-v3-content/<uuid>/files/<uuid>.pdf
    assert looks_like_object_ref(
        "bid-v3-content/1d989d134a00a19c866a3275ee85fe34/files/"
        "1d989d134a00a19c866a3275ee85fe34.pdf"
    )
    assert looks_like_object_ref("attachments/2026/announcement.docx")
    # 真 HTML / 正文不应被误判
    assert not looks_like_object_ref("<p>本项目预算 480 万元</p>")
    assert not looks_like_object_ref("本项目预算为 480 万元，服务期一年。")
    assert not looks_like_object_ref("")
    assert not looks_like_object_ref(None)


def test_attachment_content_does_not_become_body() -> None:
    ref = "bid-v3-content/1d989d134a00a19c866a3275ee85fe34/files/1d989d134a00a19c866a3275ee85fe34.pdf"
    raw = {
        "uid": "u-attachment-1",
        "title": "某市政务云协维服务采购公告",
        "content": ref,
        "publishTime": "2026-10-26 20:44:04",
    }
    item, warnings = normalize_opportunity(
        raw, fetched_at=datetime.now(UTC), include_content=True
    )

    assert item is not None
    assert item.detail_status == "attachment"
    assert item.content_html is None
    assert item.content_text is None
    assert item.content_fetched_at is None
    assert any(w.code == "content_attachment_only" for w in warnings)


def test_real_html_content_still_marks_full() -> None:
    raw = {
        "uid": "u-full-1",
        "title": "某市政务云协维服务采购公告",
        "content": "<p>本项目预算 480 万元，服务期一年。</p>",
        "publishTime": "2026-10-26 20:44:04",
    }
    item, warnings = normalize_opportunity(
        raw, fetched_at=datetime.now(UTC), include_content=True
    )

    assert item is not None
    assert item.detail_status == "full"
    assert item.content_text and "480 万元" in item.content_text
    assert not any(w.code == "content_attachment_only" for w in warnings)
