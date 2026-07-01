"""Shared FastAPI dependencies and small helpers."""
from __future__ import annotations

from functools import lru_cache

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import ledger, models
from .models import Market, Policy, User
from .providers import get_provider
from .providers.base import MarketProvider
from .schemas import PoolOut


@lru_cache(maxsize=1)
def provider() -> MarketProvider:
    """Process-wide market provider (constructed once)."""
    return get_provider()


def get_demo_user(session: Session) -> User:
    """Return (creating if needed) the single demo user."""
    user = session.scalar(select(User).where(User.username == "demo"))
    if user is None:
        user = User(username="demo")
        session.add(user)
        session.flush()
    return user


def pool_snapshot(session: Session) -> PoolOut:
    """Build the pool status response from a fresh recompute."""
    pool = ledger.recompute_pool(session)
    active = session.scalars(
        select(Policy).where(Policy.status == models.POLICY_ACTIVE)
    ).all()
    ratio = (
        pool.cash_balance / pool.reserved_liabilities
        if pool.reserved_liabilities > 0
        else float("inf")
    )
    return PoolOut(
        cash_balance=round(pool.cash_balance, 2),
        reserved_liabilities=round(pool.reserved_liabilities, 2),
        hedge_asset_value=round(pool.hedge_asset_value, 2),
        collected_premiums=round(ledger.collected_premiums(session), 2),
        solvency_ratio=(round(ratio, 4) if ratio != float("inf") else float("inf")),
        active_policies=len(active),
    )


def upsert_market(session: Session, md: dict, provider_name: str) -> Market:
    """Insert or update the cached Market row from provider data."""
    from dateutil import parser as date_parser

    market = session.scalar(
        select(Market).where(
            Market.provider == provider_name,
            Market.external_id == md["external_id"],
        )
    )
    end_date = None
    if md.get("end_date"):
        try:
            end_date = date_parser.parse(md["end_date"]).replace(tzinfo=None)
        except (ValueError, TypeError, OverflowError):
            end_date = None

    if market is None:
        market = Market(provider=provider_name, external_id=md["external_id"])
        session.add(market)
    market.question = md["question"]
    market.description = md.get("description")
    market.end_date = end_date
    # Don't clobber a locally-resolved status with a re-sync from the provider.
    if market.status in (None, models.MARKET_OPEN) or market.id is None:
        market.probability = md["probability"]
        market.status = md.get("status", models.MARKET_OPEN)
    market.synced_at = models.utcnow()
    session.flush()
    return market
