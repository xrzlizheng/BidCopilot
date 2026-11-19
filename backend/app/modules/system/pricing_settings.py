"""报价策略租户配置（pricing_cost_model / pricing_target_margins）的读写入口。

校验口径与 pricing.cost_model.parse_cost_model、pricing.service.load_target_margins
保持一致：三率 ∈ [0, 1) 且 indirect+tax < 1；人力单价 > 0；三档目标净利率 ∈ [0, 1)。
存储复用 TenantSetting，键名不变；金额/比率一律序列化为字符串（率六位小数、金额两位小数）。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.pricing.cost_model import (
    RATE_QUANT,
    decimal_or_none,
    parse_cost_model,
)
from app.modules.pricing.engine import money_text, rate_text
from app.modules.pricing.service import (
    PRICING_COST_MODEL_KEY,
    TARGET_MARGINS_KEY,
    load_target_margins,
)
from app.modules.system.models import TenantSetting
from app.modules.system.repository import SystemSettingRepository
from app.modules.system.schemas import PricingSettingsUpdate

RATE_KEYS = ("direct_rate", "indirect_rate", "tax_rate")
MARGIN_KEYS = ("conservative", "balanced", "aggressive")
MAX_LABOR_ROLES = 50
MAX_ROLE_NAME_LENGTH = 100


def _parse_rate(raw: Any, label: str) -> Decimal | None:
    if raw is None or raw == "":
        return None
    value = decimal_or_none(raw)
    if value is None or not value.is_finite():
        raise ValueError(f"{label} 必须是数字或 null")
    if value < 0 or value >= 1:
        raise ValueError(f"{label} 必须在 [0, 1) 范围内")
    quantized = value.quantize(RATE_QUANT)
    if quantized >= 1:
        raise ValueError(f"{label} 必须在 [0, 1) 范围内")
    return quantized


def _parse_labor_rate(raw: Any, name: str) -> Decimal:
    rate = decimal_or_none(raw)
    if rate is None or not rate.is_finite():
        raise ValueError(f"岗位 {name} 的日单价必须是数字")
    if rate <= 0:
        raise ValueError(f"岗位 {name} 的日单价必须大于 0")
    tup = rate.as_tuple()
    if tup.exponent < -2:
        raise ValueError(f"岗位 {name} 的日单价最多两位小数")
    # 与 pydantic max_digits=18 / decimal_places=2 同量级：整数部分最多 16 位。
    if len(tup.digits) > 18 or rate.adjusted() > 15:
        raise ValueError(f"岗位 {name} 的日单价超出精度限制（最多 18 位数字）")
    return rate


def validate_cost_model_block(raw: dict[str, Any]) -> dict[str, Any]:
    rates = {key: _parse_rate(raw.get(key), key) for key in RATE_KEYS}
    indirect = rates["indirect_rate"]
    tax = rates["tax_rate"]
    if indirect is not None and tax is not None and indirect + tax >= 1:
        raise ValueError("indirect_rate 与 tax_rate 之和必须小于 1")

    labor_raw = raw.get("labor_rates")
    if labor_raw is None:
        labor_raw = {}
    if not isinstance(labor_raw, dict):
        raise ValueError("labor_rates 必须是 {岗位: 日单价} 对象")
    if len(labor_raw) > MAX_LABOR_ROLES:
        raise ValueError(f"labor_rates 最多 {MAX_LABOR_ROLES} 个岗位")
    labor_rates: dict[str, str] = {}
    for role, rate_raw in labor_raw.items():
        name = str(role).strip()
        if not name or len(name) > MAX_ROLE_NAME_LENGTH:
            raise ValueError(f"岗位名必须为 1..{MAX_ROLE_NAME_LENGTH} 个字符")
        if name in labor_rates:
            raise ValueError(f"岗位名重复：{name}")
        labor_rates[name] = money_text(_parse_labor_rate(rate_raw, name))
    return {
        "direct_rate": rate_text(rates["direct_rate"]),
        "indirect_rate": rate_text(rates["indirect_rate"]),
        "tax_rate": rate_text(rates["tax_rate"]),
        "labor_rates": labor_rates,
    }


def validate_target_margins_block(raw: dict[str, Any]) -> dict[str, str]:
    margins: dict[str, str] = {}
    for key in MARGIN_KEYS:
        value = decimal_or_none(raw.get(key))
        if value is None or not value.is_finite():
            raise ValueError(f"target_margins 缺少或非法字段 {key}")
        if value < 0 or value >= 1:
            raise ValueError(f"target_margins.{key} 必须在 [0, 1) 范围内")
        quantized = value.quantize(RATE_QUANT)
        if quantized >= 1:
            raise ValueError(f"target_margins.{key} 必须在 [0, 1) 范围内")
        margins[key] = rate_text(quantized)
    return margins


def build_setting_updates(body: PricingSettingsUpdate) -> dict[str, dict[str, Any]]:
    """省略的块不产生更新；提供的块整体替换。"""
    updates: dict[str, dict[str, Any]] = {}
    if body.cost_model is not None:
        updates[PRICING_COST_MODEL_KEY] = validate_cost_model_block(
            body.cost_model.model_dump()
        )
    if body.target_margins is not None:
        updates[TARGET_MARGINS_KEY] = validate_target_margins_block(
            body.target_margins.model_dump()
        )
    return updates


async def get_pricing_settings(session: AsyncSession, tenant_id: UUID) -> dict[str, Any]:
    rows = list(
        (
            await session.execute(
                SystemSettingRepository(session, tenant_id)
                .scoped(select(TenantSetting))
                .where(TenantSetting.key.in_([PRICING_COST_MODEL_KEY, TARGET_MARGINS_KEY]))
            )
        ).scalars()
    )
    raw_model = next(
        (
            row.value
            for row in rows
            if row.key == PRICING_COST_MODEL_KEY and isinstance(row.value, dict)
        ),
        None,
    )
    if raw_model is not None:
        # 与引擎消费口径一致：非法存量条目按缺项处理，不静默填默认费率。
        model = parse_cost_model(
            raw_model, source=f"tenant-setting:{PRICING_COST_MODEL_KEY}"
        )
        cost_model: dict[str, Any] = {
            "direct_rate": rate_text(model.direct_rate),
            "indirect_rate": rate_text(model.indirect_rate),
            "tax_rate": rate_text(model.tax_rate),
            "labor_rates": {
                role: money_text(rate) for role, rate in model.labor_rates.items()
            },
        }
        cost_source = f"tenant-setting:{PRICING_COST_MODEL_KEY}"
    else:
        cost_model = {
            "direct_rate": None,
            "indirect_rate": None,
            "tax_rate": None,
            "labor_rates": {},
        }
        cost_source = "missing"
    margins, margins_source, _note = await load_target_margins(session, tenant_id)
    return {
        "cost_model": cost_model,
        "target_margins": {key: rate_text(margins[key]) for key in MARGIN_KEYS},
        "source": {"cost_model": cost_source, "target_margins": margins_source},
    }


async def update_pricing_settings(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    user_id: UUID,
    body: PricingSettingsUpdate,
) -> dict[str, Any]:
    updates = build_setting_updates(body)
    repository = SystemSettingRepository(session, tenant_id)
    for key, value in updates.items():
        row = (
            await session.execute(
                repository.scoped(select(TenantSetting)).where(TenantSetting.key == key)
            )
        ).scalar_one_or_none()
        if row is None:
            session.add(
                TenantSetting(
                    tenant_id=tenant_id, key=key, value=value, updated_by=user_id
                )
            )
        else:
            row.value = value
            row.updated_by = user_id
    await session.commit()
    return await get_pricing_settings(session, tenant_id)