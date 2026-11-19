from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.system.models import TenantSetting
from app.modules.system.schemas import RuntimeSettingsUpdate

DEFAULTS: dict[str, dict[str, Any]] = {
    "decision_weights": {
        "qualification": 25,
        "economics": 20,
        "cost": 20,
        "risk": 15,
        "resource": 10,
        # P1-8：竞争情报维度（历史中标/供应商家数/我方胜率）。权重从资格与经济性
        # 各让 5 分而来，六维合计仍为 100；见 projects/decision_card.py 常量注释。
        "competition": 10,
    },
    "decision_thresholds": {"invest_min": 70, "pending_min": 50},
    "decision_cost_model": {
        "direct_rate": None,
        "indirect_rate": None,
        "tax_rate": None,
        "person_day_rate": None,
    },
    "reminder_settings": {"enabled": True, "offsets": [7, 3, 1], "buffer_days": 2},
}


def _validate_weights(value: dict[str, Any]) -> dict[str, float]:
    weights = {key: float(value.get(key, default)) for key, default in DEFAULTS["decision_weights"].items()}
    if any(item < 0 or item > 100 for item in weights.values()):
        raise ValueError("weights must be between 0 and 100")
    if sum(weights.values()) <= 0:
        raise ValueError("weights must sum to a positive value")
    return weights


def _validate_thresholds(value: dict[str, Any]) -> dict[str, float]:
    thresholds = {
        key: float(value.get(key, default))
        for key, default in DEFAULTS["decision_thresholds"].items()
    }
    if any(item < 0 or item > 100 for item in thresholds.values()):
        raise ValueError("thresholds must be between 0 and 100")
    if thresholds["pending_min"] > thresholds["invest_min"]:
        raise ValueError("pending_min must not exceed invest_min")
    return thresholds


def _validate_cost_model(value: dict[str, Any]) -> dict[str, float | None]:
    model: dict[str, float | None] = {}
    for key in ("direct_rate", "indirect_rate", "tax_rate"):
        raw = value.get(key)
        if raw is None or raw == "":
            model[key] = None
            continue
        rate = float(raw)
        if rate < 0 or rate > 1:
            raise ValueError(f"{key} must be between 0 and 1")
        model[key] = rate
    person_day_rate = value.get("person_day_rate")
    if person_day_rate is None or person_day_rate == "":
        model["person_day_rate"] = None
    else:
        rate = float(person_day_rate)
        if rate <= 0 or rate > 100000:
            raise ValueError("person_day_rate must be between 0 and 100000")
        model["person_day_rate"] = rate
    if any(model[key] is not None for key in ("direct_rate", "indirect_rate", "tax_rate")) and any(
        model[key] is None for key in ("direct_rate", "indirect_rate", "tax_rate")
    ):
        raise ValueError("cost model rates must be all set or all empty")
    if model["direct_rate"] is not None:
        total = (model["direct_rate"] or 0) + (model["indirect_rate"] or 0) + (
            model["tax_rate"] or 0
        )
        if total >= 1:
            raise ValueError("cost model rates must sum to less than 1")
    return model


def _validate_reminders(value: dict[str, Any]) -> dict[str, Any]:
    offsets = value.get("offsets", DEFAULTS["reminder_settings"]["offsets"])
    if not isinstance(offsets, list):
        raise ValueError("reminder offsets must be a list")
    cleaned = sorted(
        {int(item) for item in offsets if str(item).isdigit() and 0 < int(item) <= 60},
        reverse=True,
    )
    if not cleaned:
        raise ValueError("reminder offsets must contain 1-60 day values")
    buffer_days = int(value.get("buffer_days", 2))
    if buffer_days < 0 or buffer_days > 30:
        raise ValueError("buffer_days must be between 0 and 30")
    return {
        "enabled": bool(value.get("enabled", True)),
        "offsets": cleaned,
        "buffer_days": buffer_days,
    }


async def get_runtime_settings(session: AsyncSession, tenant_id: UUID) -> dict[str, Any]:
    rows = list(
        (
            await session.execute(
                select(TenantSetting).where(
                    TenantSetting.tenant_id == tenant_id,
                    TenantSetting.key.in_(DEFAULTS.keys()),
                )
            )
        ).scalars()
    )
    values = {key: dict(value) for key, value in DEFAULTS.items()}
    configured = set()
    for row in rows:
        if isinstance(row.value, dict):
            values[row.key].update(row.value)
            configured.add(row.key)
    return {
        "weights": values["decision_weights"],
        "thresholds": values["decision_thresholds"],
        "cost_model": values["decision_cost_model"],
        "reminders": values["reminder_settings"],
        "source": {
            "weights": "tenant-setting" if "decision_weights" in configured else "tool-default",
            "thresholds": "tenant-setting"
            if "decision_thresholds" in configured
            else "tool-default",
            "cost_model": "tenant-setting"
            if "decision_cost_model" in configured
            else "missing",
            "reminders": "tenant-setting"
            if "reminder_settings" in configured
            else "tool-default",
        },
    }


async def update_runtime_settings(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    user_id: UUID,
    body: RuntimeSettingsUpdate,
) -> dict[str, Any]:
    updates: dict[str, dict[str, Any]] = {}
    if body.weights is not None:
        updates["decision_weights"] = _validate_weights(body.weights.model_dump())
    if body.thresholds is not None:
        updates["decision_thresholds"] = _validate_thresholds(body.thresholds.model_dump())
    if body.cost_model is not None:
        updates["decision_cost_model"] = _validate_cost_model(body.cost_model.model_dump())
    if body.reminders is not None:
        updates["reminder_settings"] = _validate_reminders(body.reminders.model_dump())
    for key, value in updates.items():
        row = (
            await session.execute(
                select(TenantSetting).where(
                    TenantSetting.tenant_id == tenant_id,
                    TenantSetting.key == key,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            row = TenantSetting(tenant_id=tenant_id, key=key, value=value, updated_by=user_id)
            session.add(row)
        else:
            row.value = value
            row.updated_by = user_id
    await session.commit()
    return await get_runtime_settings(session, tenant_id)
