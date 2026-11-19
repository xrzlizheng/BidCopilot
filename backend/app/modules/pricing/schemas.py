from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


class LaborInput(BaseModel):
    role: str = Field(min_length=1, max_length=100)
    days: Decimal = Field(gt=0, max_digits=12, decimal_places=2)


class PricingInputs(BaseModel):
    quote_amount: Decimal | None = Field(default=None, gt=0, max_digits=18, decimal_places=2)
    budget_amount: Decimal | None = Field(default=None, gt=0, max_digits=18, decimal_places=2)
    other_direct_cost: Decimal | None = Field(
        default=None, ge=0, max_digits=18, decimal_places=2
    )
    labor_items: list[LaborInput] = Field(default_factory=list, max_length=100)
    industry: str | None = Field(default=None, max_length=100)
    include_narrative: bool = True


class PricingStrategyRequest(BaseModel):
    inputs: PricingInputs = Field(default_factory=PricingInputs)


class QuoteItemInput(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    spec: str = Field(default="", max_length=300)
    quantity: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    unit: str = Field(min_length=1, max_length=20)
    unit_price: Decimal = Field(ge=0, max_digits=18, decimal_places=2)


class QuoteGenerateRequest(BaseModel):
    tier: Literal["conservative", "balanced", "aggressive"] | None = None
    quote_amount: Decimal | None = Field(default=None, gt=0, max_digits=18, decimal_places=2)
    items: list[QuoteItemInput] = Field(default_factory=list, max_length=200)
    supplier_name: str | None = Field(default=None, max_length=300)
    notes: str | None = Field(default=None, max_length=2000)
    validity_days: int = Field(default=90, ge=1, le=365)
