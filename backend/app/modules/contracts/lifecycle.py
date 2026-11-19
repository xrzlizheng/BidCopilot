"""合同全生命周期纯逻辑：状态机 / 金额汇总 / 到期窗口 / 变更校验。

本模块不依赖数据库，便于单测覆盖金额精度与非法状态跳转；DB 侧编排在
records_service.py，路由在 lifecycle_router.py。
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from app.core.errors import ConflictError, ValidationAppError

CONTRACT_STATUSES: frozenset[str] = frozenset(
    {"draft", "pending_approval", "active", "expired", "terminated", "archived"}
)

# 状态机的唯一事实来源：审批流必须走 draft -> pending_approval -> active；
# 到期/终止/归档只能从明确允许的状态进入，禁止路由层直接赋值 status。
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"pending_approval", "archived"}),
    "pending_approval": frozenset({"active", "draft", "archived"}),
    "active": frozenset({"expired", "terminated", "archived"}),
    "expired": frozenset({"archived"}),
    "terminated": frozenset({"archived"}),
    "archived": frozenset(),
}

ACTION_TARGET_STATUS: dict[str, str] = {
    "submit": "pending_approval",
    "approve": "active",
    "reject": "draft",
    "terminate": "terminated",
    "archive": "archived",
}

LEDGER_TYPES: frozenset[str] = frozenset({"invoice", "payment", "deposit", "retention"})
CHANGE_TYPES: frozenset[str] = frozenset({"change", "renewal"})
CHANGE_APPROVAL_STATUSES: frozenset[str] = frozenset({"pending", "approved", "rejected"})

DEFAULT_EXPIRY_REMINDER_DAYS = 30
ZERO = Decimal("0.00")
CENT = Decimal("0.01")


def ensure_known_status(status: str) -> str:
    if status not in CONTRACT_STATUSES:
        raise ValidationAppError(
            f"未知合同状态：{status}", code="invalid_contract_status"
        )
    return status


def ensure_transition(current: str, target: str) -> str:
    """校验状态跳转；非法跳转 409，未知状态 422（不静默改写）。"""
    ensure_known_status(current)
    ensure_known_status(target)
    if target not in ALLOWED_TRANSITIONS[current]:
        raise ConflictError(
            f"合同状态不允许从 {current} 跳转到 {target}",
            code="invalid_status_transition",
        )
    return target


def resolve_action(current: str, action: str) -> str:
    """把审批动作映射到目标状态并校验；未知动作 422。"""
    target = ACTION_TARGET_STATUS.get(action)
    if target is None:
        raise ValidationAppError(f"未知审批动作：{action}", code="invalid_contract_action")
    return ensure_transition(current, target)


def money_str(value: Decimal | None) -> str | None:
    """金额统一序列化为两位小数字符串；float 一律拒绝，避免二进制精度污染。"""
    if value is None:
        return None
    if isinstance(value, float):
        raise TypeError("金额必须使用 Decimal，禁止 float")
    try:
        decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("金额格式非法") from exc
    return format(decimal_value.quantize(CENT), "f")


def _field(entry: Any, name: str) -> Any:
    if isinstance(entry, dict):
        return entry.get(name)
    return getattr(entry, name)


def _amount(value: Any) -> Decimal:
    if value is None:
        return ZERO
    if isinstance(value, float):
        raise TypeError("台账金额必须使用 Decimal，禁止 float")
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("台账金额格式非法") from exc


def summarize_ledger(
    entries: Iterable[Any], *, contract_amount: Decimal | None = None
) -> dict[str, Decimal]:
    """汇总已开票/已收款/未收余额；所有中间结果使用 Decimal。

    invoice=已开票，payment=已收款，outstanding=invoice-payment（超收时为负，
    保留负号帮助财务发现错账，而不是静默截断为 0）。
    """
    totals = {key: ZERO for key in ("invoice", "payment", "deposit", "retention")}
    for entry in entries:
        entry_type = str(_field(entry, "entry_type") or "")
        if entry_type not in LEDGER_TYPES:
            raise ValueError(f"未知台账类型：{entry_type}")
        totals[entry_type] += _amount(_field(entry, "amount"))
    invoice_total = totals["invoice"].quantize(CENT)
    payment_total = totals["payment"].quantize(CENT)
    deposit_total = totals["deposit"].quantize(CENT)
    retention_total = totals["retention"].quantize(CENT)
    contract = _amount(contract_amount).quantize(CENT) if contract_amount is not None else None
    return {
        "contract_amount": contract if contract is not None else ZERO,
        "invoice_total": invoice_total,
        "payment_total": payment_total,
        "deposit_total": deposit_total,
        "retention_total": retention_total,
        "outstanding": (invoice_total - payment_total).quantize(CENT),
    }


def resolve_expiry_reminder_days(setting_value: Any) -> int:
    """解析 TenantSetting.contract_expiry_reminder，默认 30 天，限制 0..365。"""
    raw: Any = None
    if isinstance(setting_value, dict):
        raw = (
            setting_value.get("days")
            if setting_value.get("days") is not None
            else setting_value.get("value")
        )
        if raw is None:
            raw = setting_value.get("reminder_days")
    elif isinstance(setting_value, (int, str)) and not isinstance(setting_value, bool):
        raw = setting_value
    if raw is None or isinstance(raw, bool):
        return DEFAULT_EXPIRY_REMINDER_DAYS
    try:
        days = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_EXPIRY_REMINDER_DAYS
    if days < 0:
        return 0
    if days > 365:
        return 365
    return days


def expiry_reminder_window(today: date, days: int) -> date:
    return today + timedelta(days=max(0, days))


def is_in_expiry_window(expiry_date: date | None, today: date, days: int) -> bool:
    if expiry_date is None:
        return False
    return today <= expiry_date <= expiry_reminder_window(today, days)


def validate_change(
    *,
    change_type: str,
    amount_before: Decimal | None,
    amount_after: Decimal | None,
    expiry_before: date | None,
    expiry_after: date | None,
    reason: str,
) -> None:
    """变更/续签必须留下可审计的实质变化，且原因必填。"""
    if change_type not in CHANGE_TYPES:
        raise ValidationAppError(
            f"未知变更类型：{change_type}", code="invalid_change_type"
        )
    if not reason or not reason.strip():
        raise ValidationAppError("变更原因不能为空", code="change_reason_required")
    amount_changed = amount_after is not None and amount_after != amount_before
    date_changed = expiry_after is not None and expiry_after != expiry_before
    if not amount_changed and not date_changed:
        raise ValidationAppError(
            "变更必须包含金额或到期日的变化", code="empty_change"
        )
    if change_type == "renewal":
        if expiry_after is None:
            raise ValidationAppError("续签必须填写续签后的到期日", code="renewal_expiry_required")
        if expiry_before is not None and expiry_after <= expiry_before:
            raise ValidationAppError(
                "续签后的到期日必须晚于原到期日", code="renewal_expiry_not_extended"
            )
    if amount_after is not None and amount_after < 0:
        raise ValidationAppError("变更后金额不能为负数", code="negative_change_amount")


def next_change_approval(current: str, action: str) -> str:
    """变更审批：pending -> approved/rejected，审批后不可再改。"""
    if current not in CHANGE_APPROVAL_STATUSES:
        raise ValidationAppError(
            f"未知变更审批状态：{current}", code="invalid_change_status"
        )
    target = {"approve": "approved", "reject": "rejected"}.get(action)
    if target is None:
        raise ValidationAppError(f"未知变更审批动作：{action}", code="invalid_change_action")
    if current != "pending":
        raise ConflictError(
            f"变更审批状态 {current} 不能再执行 {action}",
            code="change_already_decided",
        )
    return target


def utcnow() -> datetime:
    from datetime import UTC

    return datetime.now(UTC)
