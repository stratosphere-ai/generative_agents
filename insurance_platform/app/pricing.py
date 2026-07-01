"""Pure pricing / solvency math. No I/O — trivially unit-testable.

Economic model
--------------
A binary prediction market prices event E at ``p`` in (0, 1) = implied probability.
Insuring against E for coverage C:

    premium  = C * p * (1 + loading_factor)     # market-fair price + insurer margin
    reserve  = C                                 # worst-case payout held as liability
    hedge    = buy C YES-shares at price p, cost = C * p   (the "底层资产")

If E resolves YES: hedge pays C (covers the C payout); insurer keeps C*p*loading.
If E resolves NO : hedge is worth 0 (loss C*p), no payout; premium C*p*(1+loading)
                   was collected -> net +C*p*loading.

So in expectation the book is self-funding and the profit is the loading margin.
"""
from __future__ import annotations

import math

PROB_FLOOR = 0.001
PROB_CEIL = 0.999


def clamp_probability(p: float) -> float:
    """Clamp an implied probability into (0, 1) to avoid degenerate pricing."""
    if math.isnan(p):
        raise ValueError("probability is NaN")
    return max(PROB_FLOOR, min(PROB_CEIL, p))


def calc_premium(coverage: float, p: float, loading_factor: float) -> float:
    """premium = C * p * (1 + loading_factor)."""
    if coverage <= 0:
        raise ValueError("coverage must be positive")
    if loading_factor < 0:
        raise ValueError("loading_factor must be non-negative")
    return coverage * clamp_probability(p) * (1.0 + loading_factor)


def calc_reserve(coverage: float) -> float:
    """Reserve equals the full worst-case payout."""
    if coverage <= 0:
        raise ValueError("coverage must be positive")
    return coverage


def calc_hedge_cost(coverage: float, p: float) -> float:
    """Cost of buying ``coverage`` YES-shares at market price p."""
    if coverage <= 0:
        raise ValueError("coverage must be positive")
    return coverage * clamp_probability(p)


def solvency_ratio(cash_balance: float, reserved_liabilities: float) -> float:
    """cash / reserved. Infinite when there are no liabilities."""
    if reserved_liabilities <= 0:
        return math.inf
    return cash_balance / reserved_liabilities


def can_underwrite(
    cash_balance: float,
    reserved_liabilities: float,
    new_reserve: float,
    min_ratio: float,
) -> bool:
    """Whether adding ``new_reserve`` keeps solvency at/above ``min_ratio``.

    Underwriting adds the new reserve to liabilities. Cash is unchanged at
    decision time (premium in ~= hedge out), so we test the projected ratio.
    """
    projected = solvency_ratio(cash_balance, reserved_liabilities + new_reserve)
    return projected >= min_ratio


# --- Basket (cargo) helpers: a shipment is hedged as a portfolio of factors. ---

def factor_covered_loss(cargo_value: float, impact: float) -> float:
    """Dollar loss put at risk by one factor = impact fraction of cargo value."""
    if cargo_value <= 0:
        raise ValueError("cargo_value must be positive")
    if not 0 < impact <= 1:
        raise ValueError("impact must be in (0, 1]")
    return cargo_value * impact


def factor_expected_loss(cargo_value: float, impact: float, p: float) -> float:
    """Probability-weighted loss for one factor."""
    return factor_covered_loss(cargo_value, impact) * clamp_probability(p)


def basket_premium(assessments) -> float:
    """Total premium of a basket = sum of per-factor premiums.

    P1 assumes factors are independent and their losses are additive; joint-loss
    modelling and a cap at cargo value are deferred to P3 (correlation + basis).
    """
    return sum(a.premium for a in assessments)
