from typing import Literal

from pydantic import BaseModel, Field


class ModelConfigUpdate(BaseModel):
    base_url: str | None = None
    model: str | None = None
    enabled: bool = True
    api_key: str | None = None


class ModelConfigResponse(BaseModel):
    purpose: str
    scope: str = "tenant"
    provider: str
    base_url: str | None
    model: str | None
    enabled: bool
    effective_base_url: str | None
    effective_model: str | None
    configured: bool
    overridden: bool
    key_last4: str | None = None
    credential_scope: str = "tenant"


class ModelPolicyResponse(BaseModel):
    byok_enabled: bool
    allow_personal_key_for_enterprise_data: bool


class ModelPolicyUpdate(BaseModel):
    allow_personal_key_for_enterprise_data: bool = False


class RuntimeWeights(BaseModel):
    qualification: float = 25
    economics: float = 20
    cost: float = 20
    risk: float = 15
    resource: float = 10
    # 竞争情报（采购人历史中标/供应商家数/我方胜率）：六维合计 100。
    competition: float = 10


class RuntimeThresholds(BaseModel):
    invest_min: float = 70
    pending_min: float = 50


class RuntimeCostModel(BaseModel):
    direct_rate: float | None = None
    indirect_rate: float | None = None
    tax_rate: float | None = None
    person_day_rate: float | None = None


class RuntimeReminders(BaseModel):
    enabled: bool = True
    offsets: list[int] = [7, 3, 1]
    buffer_days: int = 2


class RuntimeSettingsUpdate(BaseModel):
    weights: RuntimeWeights | None = None
    thresholds: RuntimeThresholds | None = None
    cost_model: RuntimeCostModel | None = None
    reminders: RuntimeReminders | None = None


class PricingCostModelBlock(BaseModel):
    # 语义校验（范围/精度/岗位名）在 pricing_settings 服务层做，
    # 失败统一抛 ValidationAppError，保证 422 错误体为 {"code","message"}。
    direct_rate: str | int | float | None = None
    indirect_rate: str | int | float | None = None
    tax_rate: str | int | float | None = None
    labor_rates: dict[str, str | int | float | None] = Field(default_factory=dict)


class PricingTargetMarginsBlock(BaseModel):
    conservative: str | int | float | None = None
    balanced: str | int | float | None = None
    aggressive: str | int | float | None = None


class PricingSettingsUpdate(BaseModel):
    cost_model: PricingCostModelBlock | None = None
    target_margins: PricingTargetMarginsBlock | None = None


ModelPurpose = Literal["main", "judge"]
