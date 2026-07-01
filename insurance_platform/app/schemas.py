"""Pydantic request/response models for the API layer."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class MarketOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    provider: str
    external_id: str
    question: str
    description: str | None = None
    probability: float
    end_date: datetime | None = None
    status: str


class QuoteRequest(BaseModel):
    market_id: int
    coverage: float = Field(gt=0, description="Payout amount if the event resolves YES")


class QuoteResponse(BaseModel):
    market_id: int
    question: str
    coverage: float
    probability: float
    loading_factor: float
    premium: float
    reserve: float


class PolicyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    market_id: int
    coverage: float
    probability_at_purchase: float
    loading_factor: float
    premium: float
    reserve: float
    status: str
    created_at: datetime
    resolved_at: datetime | None = None
    question: str | None = None


class PoolOut(BaseModel):
    cash_balance: float
    reserved_liabilities: float
    hedge_asset_value: float
    collected_premiums: float
    solvency_ratio: float
    active_policies: int


class BuyResponse(BaseModel):
    policy_id: int
    premium: float
    coverage: float
    status: str
    pool_after: PoolOut


class AdminMarketOut(MarketOut):
    active_policies: int
    total_exposure: float
    resolvable: bool


class ResolveRequest(BaseModel):
    market_id: int
    outcome: str = Field(pattern="^(yes|no|YES|NO|Yes|No)$")


class ResolveResponse(BaseModel):
    market_id: int
    outcome: str
    policies_paid: int
    policies_expired: int
    legs_paid: int = 0
    legs_expired: int = 0
    total_payout: float
    pool_after: PoolOut


# --- Cargo basket (shipment) product ---

class ShipmentQuoteRequest(BaseModel):
    cargo_value: float = Field(gt=0, description="Insured value of the cargo, e.g. 400000")
    origin: str
    destination: str
    deadline: datetime | None = None
    cargo_type: str | None = None
    route: str | None = None


class FactorOut(BaseModel):
    key: str
    label: str
    category: str
    probability: float
    impact: float
    covered_loss: float
    expected_loss: float
    hedge_cost: float
    premium: float
    source: str = "rule"            # discovery source: rule | llm
    market_provider: str = "engine"  # engine | polymarket | kalshi
    matched: bool = False            # mapped to a real order book?


class ShipmentQuoteResponse(BaseModel):
    cargo_value: float
    origin: str
    destination: str
    loading_factor: float
    premium: float
    total_covered: float
    expected_loss_total: float
    factor_count: int
    discovery_mode: str = "rule"     # rule | llm
    matched_count: int = 0           # factors mapped to a real book
    factors: list[FactorOut]


class BasketBuyResponse(BaseModel):
    policy_id: int
    premium: float
    total_covered: float
    factor_count: int
    status: str
    pool_after: PoolOut


class HedgeLegOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    factor_key: str
    factor_label: str
    category: str
    impact: float
    covered_loss: float
    probability_at_purchase: float
    premium: float
    source: str = "rule"
    market_provider: str = "engine"
    status: str


class BasketPolicyOut(BaseModel):
    id: int
    premium: float
    total_covered: float
    total_reserve: float
    status: str
    created_at: datetime
    cargo_value: float
    origin: str
    destination: str
    deadline: datetime | None = None
    cargo_type: str | None = None
    legs: list[HedgeLegOut]
