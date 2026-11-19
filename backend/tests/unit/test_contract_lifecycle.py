"""CLM 精简版单测：状态机 / 台账 Decimal 精度 / 变更留痕 / 起草来源 / 到期提醒 / xlsx。

不依赖数据库；集成 API 用例见 tests/integration/test_contract_lifecycle_api.py。
"""

from datetime import date
from decimal import Decimal
from io import BytesIO

import pytest
from openpyxl import load_workbook
from pydantic import ValidationError

from app.core.errors import ConflictError, ValidationAppError
from app.modules.contracts.drafting import (
    CLAUSE_TEMPLATES,
    DRAFT_DISCLAIMER,
    build_contract_draft,
)
from app.modules.contracts.lifecycle import (
    ensure_transition,
    expiry_reminder_window,
    is_in_expiry_window,
    money_str,
    next_change_approval,
    resolve_action,
    resolve_expiry_reminder_days,
    summarize_ledger,
    validate_change,
)
from app.modules.contracts.lifecycle_export import build_ledger_xlsx
from app.modules.contracts.schemas import ContractRecordCreate, LedgerEntryCreate

# ---------------------------------------------------------------------------
# 状态机
# ---------------------------------------------------------------------------


def test_happy_path_draft_to_pending_to_active() -> None:
    assert resolve_action("draft", "submit") == "pending_approval"
    assert resolve_action("pending_approval", "approve") == "active"
    assert resolve_action("pending_approval", "reject") == "draft"


def test_illegal_transition_is_409_with_explicit_code() -> None:
    with pytest.raises(ConflictError) as exc:
        ensure_transition("active", "pending_approval")
    assert exc.value.status_code == 409
    assert exc.value.code == "invalid_status_transition"


def test_approve_from_draft_is_rejected() -> None:
    with pytest.raises(ConflictError) as exc:
        resolve_action("draft", "approve")
    assert exc.value.status_code == 409
    assert "draft" in exc.value.message and "active" in exc.value.message


def test_unknown_status_is_422() -> None:
    with pytest.raises(ValidationAppError) as exc:
        ensure_transition("draft", "signed")
    assert exc.value.status_code == 422
    assert exc.value.code == "invalid_contract_status"


def test_unknown_action_is_422() -> None:
    with pytest.raises(ValidationAppError) as exc:
        resolve_action("draft", "sign")
    assert exc.value.status_code == 422
    assert exc.value.code == "invalid_contract_action"


def test_terminal_states_cannot_jump_back() -> None:
    for terminal in ("archived", "terminated", "expired"):
        with pytest.raises(ConflictError):
            ensure_transition(terminal, "active")


def test_archive_is_allowed_from_most_states_but_terminal_archived() -> None:
    for current in ("draft", "pending_approval", "active", "expired", "terminated"):
        assert ensure_transition(current, "archived") == "archived"
    with pytest.raises(ConflictError):
        ensure_transition("archived", "draft")


# ---------------------------------------------------------------------------
# 金额与台账
# ---------------------------------------------------------------------------


def test_money_str_uses_two_decimals_and_rejects_float() -> None:
    assert money_str(Decimal("1000")) == "1000.00"
    assert money_str(Decimal("0.1")) == "0.10"
    assert money_str(None) is None
    with pytest.raises(TypeError):
        money_str(0.1)  # type: ignore[arg-type]


def test_ledger_summary_decimal_precision_01_plus_02() -> None:
    entries = [
        {"entry_type": "invoice", "amount": Decimal("0.10")},
        {"entry_type": "invoice", "amount": Decimal("0.20")},
    ]
    summary = summarize_ledger(entries, contract_amount=Decimal("0.30"))
    assert summary["invoice_total"] == Decimal("0.30")
    assert summary["outstanding"] == Decimal("0.30")
    assert money_str(summary["invoice_total"]) == "0.30"


def test_ledger_summary_invoice_payment_outstanding() -> None:
    entries = [
        {"entry_type": "invoice", "amount": Decimal("100.10")},
        {"entry_type": "payment", "amount": Decimal("60.05")},
        {"entry_type": "deposit", "amount": Decimal("10.00")},
        {"entry_type": "retention", "amount": Decimal("5.50")},
    ]
    summary = summarize_ledger(entries, contract_amount=Decimal("200.00"))
    assert money_str(summary["invoice_total"]) == "100.10"
    assert money_str(summary["payment_total"]) == "60.05"
    assert money_str(summary["outstanding"]) == "40.05"
    assert money_str(summary["deposit_total"]) == "10.00"
    assert money_str(summary["retention_total"]) == "5.50"
    assert money_str(summary["contract_amount"]) == "200.00"


def test_ledger_summary_overpayment_keeps_negative_outstanding() -> None:
    entries = [
        {"entry_type": "invoice", "amount": Decimal("80.00")},
        {"entry_type": "payment", "amount": Decimal("95.50")},
    ]
    summary = summarize_ledger(entries)
    assert money_str(summary["outstanding"]) == "-15.50"


def test_ledger_summary_empty_is_zero_strings() -> None:
    summary = summarize_ledger([], contract_amount=None)
    assert {key: money_str(value) for key, value in summary.items()} == {
        "contract_amount": "0.00",
        "invoice_total": "0.00",
        "payment_total": "0.00",
        "deposit_total": "0.00",
        "retention_total": "0.00",
        "outstanding": "0.00",
    }


def test_ledger_summary_rejects_float_entry() -> None:
    with pytest.raises(TypeError):
        summarize_ledger([{"entry_type": "invoice", "amount": 0.1}])


def test_ledger_summary_rejects_unknown_type() -> None:
    with pytest.raises(ValueError):
        summarize_ledger([{"entry_type": "refund", "amount": Decimal("1.00")}])


# ---------------------------------------------------------------------------
# 变更/续签
# ---------------------------------------------------------------------------


def test_change_requires_actual_difference() -> None:
    with pytest.raises(ValidationAppError) as exc:
        validate_change(
            change_type="change",
            amount_before=Decimal("100.00"),
            amount_after=Decimal("100.00"),
            expiry_before=date(2027, 1, 1),
            expiry_after=date(2027, 1, 1),
            reason="同步条款",
        )
    assert exc.value.code == "empty_change"


def test_change_requires_reason() -> None:
    with pytest.raises(ValidationAppError) as exc:
        validate_change(
            change_type="change",
            amount_before=Decimal("100.00"),
            amount_after=Decimal("120.00"),
            expiry_before=None,
            expiry_after=None,
            reason="   ",
        )
    assert exc.value.code == "change_reason_required"


def test_renewal_must_extend_expiry() -> None:
    with pytest.raises(ValidationAppError) as exc:
        validate_change(
            change_type="renewal",
            amount_before=None,
            amount_after=None,
            expiry_before=date(2027, 1, 1),
            expiry_after=date(2026, 12, 31),
            reason="续签一年",
        )
    assert exc.value.code == "renewal_expiry_not_extended"

    validate_change(
        change_type="renewal",
        amount_before=None,
        amount_after=None,
        expiry_before=date(2027, 1, 1),
        expiry_after=date(2028, 1, 1),
        reason="续签一年",
    )


def test_change_approval_is_one_way() -> None:
    assert next_change_approval("pending", "approve") == "approved"
    assert next_change_approval("pending", "reject") == "rejected"
    with pytest.raises(ConflictError) as exc:
        next_change_approval("approved", "reject")
    assert exc.value.code == "change_already_decided"


def test_negative_change_amount_rejected() -> None:
    with pytest.raises(ValidationAppError):
        validate_change(
            change_type="change",
            amount_before=Decimal("100.00"),
            amount_after=Decimal("-1.00"),
            expiry_before=None,
            expiry_after=None,
            reason="调整",
        )


# ---------------------------------------------------------------------------
# 起草：来源与免责声明
# ---------------------------------------------------------------------------


def _record() -> dict:
    return {
        "project_id": "11111111-1111-1111-1111-111111111111",
        "review_id": "22222222-2222-2222-2222-222222222222",
        "contract_no": "HT-2026-001",
        "title": "某政务云运维合同",
        "amount": Decimal("1000000.00"),
        "currency": "CNY",
    }


def _commitments() -> list[dict]:
    return [
        {
            "id": "33333333-3333-3333-3333-333333333333",
            "kind": "price",
            "label": "投标报价",
            "value_text": "1000000 元",
            "value_amount": Decimal("1000000.00"),
            "value_days": None,
        },
        {
            "id": "44444444-4444-4444-4444-444444444444",
            "kind": "schedule",
            "label": "承诺工期",
            "value_text": "90 天",
            "value_amount": None,
            "value_days": 90,
        },
        {
            "id": "55555555-5555-5555-5555-555555555555",
            "kind": "warranty",
            "label": "承诺质保期",
            "value_text": "365 天",
            "value_amount": None,
            "value_days": 365,
        },
    ]


def test_draft_has_disclaimer_and_every_clause_has_source() -> None:
    clauses = build_contract_draft(record=_record(), commitments=_commitments())
    assert clauses
    assert any(DRAFT_DISCLAIMER in clause["text"] for clause in clauses)
    for clause in clauses:
        assert clause["source_json"]
        assert clause["source_json"].get("type")


def test_draft_commitment_clauses_trace_to_commitment_id() -> None:
    clauses = build_contract_draft(record=_record(), commitments=_commitments())
    commitment_ids = {
        clause["source_json"].get("commitment_id")
        for clause in clauses
        if clause["source_json"].get("type") == "bid_commitment"
    }
    assert "33333333-3333-3333-3333-333333333333" in commitment_ids
    assert "44444444-4444-4444-4444-444444444444" in commitment_ids
    assert any(
        clause["source_json"].get("template_id") == "payment_terms" for clause in clauses
    )


def test_draft_includes_all_template_library_clauses() -> None:
    clauses = build_contract_draft(record=_record())
    template_ids = {
        clause["source_json"].get("template_id")
        for clause in clauses
        if clause["source_json"].get("type") == "clause_template"
    }
    assert {template["template_id"] for template in CLAUSE_TEMPLATES} <= template_ids


def test_draft_review_clauses_keep_clause_source() -> None:
    review_clauses = [
        {
            "id": "66666666-6666-6666-6666-666666666666",
            "review_id": "22222222-2222-2222-2222-222222222222",
            "seq": 3,
            "title": "第三条 验收",
            "text": "验收需出具书面意见。",
            "category": "acceptance",
        }
    ]
    clauses = build_contract_draft(
        record=_record(), commitments=_commitments(), review_clauses=review_clauses
    )
    traced = [c for c in clauses if c["source_json"].get("type") == "contract_review_clause"]
    assert traced and traced[0]["source_json"]["clause_id"] == "66666666-6666-6666-6666-666666666666"
    assert traced[0]["source_json"]["review_id"] == "22222222-2222-2222-2222-222222222222"


def test_draft_without_commitments_still_uses_template_price_source() -> None:
    clauses = build_contract_draft(record=_record())
    price = next(clause for clause in clauses if clause["category"] == "price")
    assert price["source_json"]["template_id"] == "price_terms"
    assert "1000000.00" in price["text"]


# ---------------------------------------------------------------------------
# 到期提醒配置
# ---------------------------------------------------------------------------


def test_expiry_reminder_defaults_to_30_days() -> None:
    assert resolve_expiry_reminder_days(None) == 30
    assert resolve_expiry_reminder_days({}) == 30
    assert resolve_expiry_reminder_days({"days": "bad"}) == 30


def test_expiry_reminder_reads_tenant_setting_dict() -> None:
    assert resolve_expiry_reminder_days({"days": 45}) == 45
    assert resolve_expiry_reminder_days({"value": "7"}) == 7
    assert resolve_expiry_reminder_days({"reminder_days": 60}) == 60


def test_expiry_reminder_clamps_range() -> None:
    assert resolve_expiry_reminder_days({"days": -5}) == 0
    assert resolve_expiry_reminder_days({"days": 9999}) == 365
    assert resolve_expiry_reminder_days(True) == 30


def test_expiry_window_boundaries() -> None:
    today = date(2026, 11, 1)
    assert expiry_reminder_window(today, 30) == date(2026, 12, 1)
    assert is_in_expiry_window(date(2026, 11, 1), today, 30)
    assert is_in_expiry_window(date(2026, 12, 1), today, 30)
    assert not is_in_expiry_window(date(2026, 12, 2), today, 30)
    assert not is_in_expiry_window(None, today, 30)


# ---------------------------------------------------------------------------
# xlsx 导出：表头/合计/公式注入
# ---------------------------------------------------------------------------


def _xlsx_bytes(
    *,
    title: str = "某政务云运维合同",
    note: str | None = None,
) -> bytes:
    return build_ledger_xlsx(
        contract_no="HT-2026-001",
        title=title,
        currency="CNY",
        entries=[
            {
                "entry_type": "invoice",
                "amount": Decimal("100.10"),
                "occurred_on": date(2026, 11, 1),
                "note": note or "首期开票",
                "voucher_ref": "FP-001",
                "created_at": "2026-11-01T09:00:00+00:00",
            },
            {
                "entry_type": "payment",
                "amount": Decimal("60.05"),
                "occurred_on": date(2026, 11, 5),
                "note": "首期回款",
                "voucher_ref": "SK-001",
                "created_at": "2026-11-05T09:00:00+00:00",
            },
        ],
        summary={
            "contract_amount": Decimal("200.00"),
            "invoice_total": Decimal("100.10"),
            "payment_total": Decimal("60.05"),
            "deposit_total": Decimal("0.00"),
            "retention_total": Decimal("0.00"),
            "outstanding": Decimal("40.05"),
        },
        disclaimer="系统生成的履约台账，需财务复核",
    )


def test_ledger_xlsx_has_header_and_total_rows() -> None:
    workbook = load_workbook(BytesIO(_xlsx_bytes()))
    sheet = workbook["履约台账"]
    assert [cell.value for cell in sheet[1]] == ["序号", "类型", "金额", "发生日", "备注", "凭证引用", "创建时间"]
    labels = [sheet.cell(row=row, column=1).value for row in range(1, sheet.max_row + 1)]
    assert "合计" in labels
    assert "已开票合计" in labels
    assert "已收款合计" in labels
    assert "未收余额" in labels
    total_row = labels.index("合计") + 1
    assert Decimal(str(sheet.cell(row=total_row, column=3).value)) == Decimal("160.15")
    workbook.close()


def test_ledger_xlsx_blocks_formula_injection_in_note_and_title() -> None:
    payload = _xlsx_bytes(
        title="@SUM(1+1)",
        note='=HYPERLINK("http://evil")',
    )
    workbook = load_workbook(BytesIO(payload))
    ledger = workbook["履约台账"]
    assert ledger["E2"].data_type == "s"
    assert ledger["E2"].value.startswith("'=")
    overview = workbook["合同概要"]
    title_cell = overview["B3"]
    assert title_cell.data_type == "s"
    assert title_cell.value.startswith("'@")
    workbook.close()

def test_record_create_schema_rejects_float_amount_and_bad_dates() -> None:
    with pytest.raises(ValidationError):
        ContractRecordCreate(
            project_id="11111111-1111-1111-1111-111111111111",
            contract_no="HT-001",
            title="测试合同",
            amount=0.1,  # type: ignore[arg-type]
        )
    with pytest.raises(ValidationError):
        ContractRecordCreate(
            project_id="11111111-1111-1111-1111-111111111111",
            contract_no="HT-001",
            title="测试合同",
            effective_date=date(2027, 1, 1),
            expiry_date=date(2026, 1, 1),
        )


def test_ledger_entry_schema_quantizes_amount_string_and_rejects_float() -> None:
    entry = LedgerEntryCreate(
        entry_type="invoice",
        amount="0.1",  # type: ignore[arg-type]
        occurred_on=date(2026, 11, 1),
    )
    assert entry.amount == Decimal("0.10")
    with pytest.raises(ValidationError):
        LedgerEntryCreate(
            entry_type="invoice",
            amount=0.1,  # type: ignore[arg-type]
            occurred_on=date(2026, 11, 1),
        )