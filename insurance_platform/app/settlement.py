"""Underwriting and resolution/settlement orchestration.

Covers both products:
- single-event policies (``underwrite_policy``) — 1 policy = 1 market YES position
- cargo basket policies (``underwrite_basket``) — 1 policy = N hedged risk factors

``resolve_market`` settles BOTH a market's single-event policies and any basket
hedge legs that reference it, so one admin action clears every exposure on a book.

These functions mutate the DB inside the caller's session transaction. They raise
``InsolvencyError`` / ``ValueError`` on invalid input; the API layer maps those to
409 / 400.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import ledger, models, pricing
from .models import BasketPolicy, HedgeLeg, Market, Policy, Shipment, User


class InsolvencyError(Exception):
    """Raised when underwriting would breach the solvency floor."""


@dataclass
class ResolutionResult:
    market_id: int
    outcome: str  # "yes" | "no"
    policies_paid: int = 0
    policies_expired: int = 0
    legs_paid: int = 0
    legs_expired: int = 0
    total_payout: float = 0.0


# ---------------------------------------------------------------------------
# Single-event product
# ---------------------------------------------------------------------------
def underwrite_policy(
    session: Session,
    user: User,
    market: Market,
    coverage: float,
    loading_factor: float,
    min_ratio: float,
) -> Policy:
    """Price, solvency-check, and issue a single-event policy."""
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


# ---------------------------------------------------------------------------
# Cargo basket product
# ---------------------------------------------------------------------------
def _upsert_factor_market(session: Session, assessment) -> Market:
    """Get/create the synthetic 'order book' Market backing a risk factor.

    The book's provider is the assessment's market_provider ('engine' for a
    synthetic P1 book, or 'polymarket'/'kalshi' when matched to a real book).
    The same factor across shipments reuses one Market row so resolving it
    settles every leg.
    """
    provider = getattr(assessment, "market_provider", "engine")
    market = session.scalar(
        select(Market).where(
            Market.provider == provider,
            Market.external_id == assessment.market_external_id,
        )
    )
    if market is None:
        market = Market(
            provider=provider,
            external_id=assessment.market_external_id,
            question=assessment.market_question,
            probability=assessment.probability,
            status=models.MARKET_OPEN,
        )
        session.add(market)
        session.flush()
    return market


def underwrite_basket(
    session: Session,
    user: User,
    shipment: Shipment,
    assessments: list,
    loading_factor: float,
    min_ratio: float,
) -> BasketPolicy:
    """Issue one basket policy hedging a shipment across N risk factors."""
    if not assessments:
        raise ValueError("no risk factors to hedge for this shipment")

    total_premium = pricing.basket_premium(assessments)
    total_covered = sum(a.covered_loss for a in assessments)
    total_reserve = total_covered  # worst case: every factor fires (P1 additive)

    pool = ledger.recompute_pool(session)
    if not pricing.can_underwrite(
        pool.cash_balance, pool.reserved_liabilities, total_reserve, min_ratio
    ):
        raise InsolvencyError(
            "underwriting this shipment would breach the solvency floor"
        )

    policy = BasketPolicy(
        user_id=user.id,
        shipment_id=shipment.id,
        premium=total_premium,
        total_covered=total_covered,
        total_reserve=total_reserve,
        loading_factor=loading_factor,
        status=models.BASKET_ACTIVE,
    )
    session.add(policy)
    session.flush()

    # One premium line for the whole policy; hedge + reserve booked per leg.
    ledger.post(
        session, models.ENTRY_PREMIUM_IN, total_premium,
        memo="basket premium", basket_policy_id=policy.id,
    )
    for a in assessments:
        market = _upsert_factor_market(session, a)
        leg = HedgeLeg(
            basket_policy_id=policy.id,
            market_id=market.id,
            factor_key=a.key,
            factor_label=a.label,
            category=a.category,
            impact=a.impact,
            covered_loss=a.covered_loss,
            probability_at_purchase=a.probability,
            premium=a.premium,
            hedge_shares=(getattr(a, "hedge_shares", 0.0) or a.covered_loss),
            hedge_price=a.probability,
            source=getattr(a, "source", "rule"),
            status=models.LEG_ACTIVE,
        )
        session.add(leg)
        session.flush()
        ledger.post(
            session, models.ENTRY_HEDGE_BUY, -a.hedge_cost,
            memo=f"hedge {a.key}", basket_policy_id=policy.id,
        )
        ledger.post(
            session, models.ENTRY_RESERVE_ADD, a.covered_loss,
            memo=f"reserve {a.key}", basket_policy_id=policy.id,
        )

    ledger.recompute_pool(session)
    return policy


def _settle_basket_legs(session: Session, market: Market, outcome: str, result: ResolutionResult) -> None:
    legs = session.scalars(
        select(HedgeLeg).where(
            HedgeLeg.market_id == market.id, HedgeLeg.status == models.LEG_ACTIVE
        )
    ).all()
    touched_policies = set()
    for leg in legs:
        if outcome == "yes":
            ledger.post(
                session, models.ENTRY_PAYOUT, -leg.covered_loss,
                memo=f"claim {leg.factor_key}", basket_policy_id=leg.basket_policy_id,
            )
            ledger.post(
                session, models.ENTRY_HEDGE_SETTLE, leg.hedge_shares,
                memo=f"hedge settled {leg.factor_key}", basket_policy_id=leg.basket_policy_id,
            )
            leg.status = models.LEG_PAID_OUT
            result.total_payout += leg.covered_loss
            result.legs_paid += 1
        else:
            leg.status = models.LEG_EXPIRED_WORTHLESS
            result.legs_expired += 1
        ledger.post(
            session, models.ENTRY_RESERVE_RELEASE, -leg.covered_loss,
            memo=f"reserve released {leg.factor_key}", basket_policy_id=leg.basket_policy_id,
        )
        leg.resolved_at = models.utcnow()
        touched_policies.add(leg.basket_policy_id)

    # Roll up each affected basket policy's status.
    for pid in touched_policies:
        policy = session.get(BasketPolicy, pid)
        remaining = session.scalar(
            select(HedgeLeg).where(
                HedgeLeg.basket_policy_id == pid, HedgeLeg.status == models.LEG_ACTIVE
            )
        )
        if remaining is None:
            policy.status = models.BASKET_SETTLED
        else:
            policy.status = models.BASKET_PARTIALLY_SETTLED


def resolve_market(session: Session, market: Market, outcome: str) -> ResolutionResult:
    """Resolve a market and settle every exposure on it (policies + basket legs)."""
    outcome = outcome.lower()
    if outcome not in ("yes", "no"):
        raise ValueError("outcome must be 'yes' or 'no'")
    if market.status != models.MARKET_OPEN:
        raise ValueError("market already resolved/closed")

    market.status = (
        models.MARKET_RESOLVED_YES if outcome == "yes" else models.MARKET_RESOLVED_NO
    )
    result = ResolutionResult(market_id=market.id, outcome=outcome)

    # 1) single-event policies
    active = session.scalars(
        select(Policy).where(
            Policy.market_id == market.id, Policy.status == models.POLICY_ACTIVE
        )
    ).all()
    for policy in active:
        if outcome == "yes":
            ledger.post(session, models.ENTRY_PAYOUT, -policy.coverage, policy.id, "claim payout")
            ledger.post(
                session, models.ENTRY_HEDGE_SETTLE, policy.hedge_shares,
                policy.id, "hedge settled at 1.0",
            )
            policy.status = models.POLICY_PAID_OUT
            result.total_payout += policy.coverage
            result.policies_paid += 1
        else:
            policy.status = models.POLICY_EXPIRED_WORTHLESS
            result.policies_expired += 1
        ledger.post(
            session, models.ENTRY_RESERVE_RELEASE, -policy.reserve, policy.id, "reserve released"
        )
        policy.resolved_at = models.utcnow()

    # 2) cargo basket hedge legs
    _settle_basket_legs(session, market, outcome, result)

    ledger.recompute_pool(session)
    return result
