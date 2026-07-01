"""Underwriting and resolution/settlement orchestration.

These functions mutate the DB (policies, ledger, pool) inside the caller's
session transaction. They raise ``InsolvencyError`` / ``ValueError`` on invalid
input; the API layer maps those to 409 / 400.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import ledger, models, pricing
from .models import Market, Policy, User


class InsolvencyError(Exception):
    """Raised when underwriting a policy would breach the solvency floor."""


@dataclass
class ResolutionResult:
    market_id: int
    outcome: str  # "yes" | "no"
    policies_paid: int
    policies_expired: int
    total_payout: float


def underwrite_policy(
    session: Session,
    user: User,
    market: Market,
    coverage: float,
    loading_factor: float,
    min_ratio: float,
) -> Policy:
    """Price, solvency-check, and issue a policy. Records premium/hedge/reserve."""
    if coverage <= 0:
        raise ValueError("coverage must be positive")
    if market.status != models.MARKET_OPEN:
        raise ValueError("market is not open for underwriting")

    p = pricing.clamp_probability(market.probability)
    premium = pricing.calc_premium(coverage, p, loading_factor)
    reserve = pricing.calc_reserve(coverage)
    hedge_cost = pricing.calc_hedge_cost(coverage, p)

    pool = ledger.recompute_pool(session)
    if not pricing.can_underwrite(
        pool.cash_balance, pool.reserved_liabilities, reserve, min_ratio
    ):
        raise InsolvencyError(
            "underwriting this policy would breach the solvency floor"
        )

    policy = Policy(
        user_id=user.id,
        market_id=market.id,
        coverage=coverage,
        probability_at_purchase=p,
        loading_factor=loading_factor,
        premium=premium,
        reserve=reserve,
        hedge_shares=coverage,
        hedge_price=p,
        status=models.POLICY_ACTIVE,
    )
    session.add(policy)
    session.flush()

    ledger.post(session, models.ENTRY_PREMIUM_IN, premium, policy.id, "premium collected")
    ledger.post(session, models.ENTRY_HEDGE_BUY, -hedge_cost, policy.id, "hedge YES shares")
    ledger.post(session, models.ENTRY_RESERVE_ADD, reserve, policy.id, "reserve liability")
    ledger.recompute_pool(session)
    return policy


def resolve_market(session: Session, market: Market, outcome: str) -> ResolutionResult:
    """Resolve a market and settle every active policy on it."""
    outcome = outcome.lower()
    if outcome not in ("yes", "no"):
        raise ValueError("outcome must be 'yes' or 'no'")
    if market.status != models.MARKET_OPEN:
        raise ValueError("market already resolved/closed")

    market.status = (
        models.MARKET_RESOLVED_YES if outcome == "yes" else models.MARKET_RESOLVED_NO
    )

    active = session.scalars(
        select(Policy).where(
            Policy.market_id == market.id, Policy.status == models.POLICY_ACTIVE
        )
    ).all()

    paid = expired = 0
    total_payout = 0.0
    for policy in active:
        if outcome == "yes":
            ledger.post(
                session, models.ENTRY_PAYOUT, -policy.coverage, policy.id, "claim payout"
            )
            ledger.post(
                session,
                models.ENTRY_HEDGE_SETTLE,
                policy.hedge_shares,  # YES shares each redeem for 1.0
                policy.id,
                "hedge settled at 1.0",
            )
            policy.status = models.POLICY_PAID_OUT
            total_payout += policy.coverage
            paid += 1
        else:
            # Hedge shares expire worthless; already expensed at buy time.
            policy.status = models.POLICY_EXPIRED_WORTHLESS
            expired += 1
        # Release the reserve either way.
        ledger.post(
            session, models.ENTRY_RESERVE_RELEASE, -policy.reserve, policy.id, "reserve released"
        )
        policy.resolved_at = models.utcnow()

    ledger.recompute_pool(session)
    return ResolutionResult(
        market_id=market.id,
        outcome=outcome,
        policies_paid=paid,
        policies_expired=expired,
        total_payout=total_payout,
    )
