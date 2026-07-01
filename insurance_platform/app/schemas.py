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
    total_payout: float
    pool_after: PoolOut
