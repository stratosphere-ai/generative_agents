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
