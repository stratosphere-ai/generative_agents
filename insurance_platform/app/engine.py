"""P2 orchestration: discover factors -> match to real books -> price the basket.

This is the seam that unifies P1 and P2. ``assess`` runs the configured discovery
strategy (rule or LLM+fallback), optionally maps each factor to a real, *liquid*
prediction-market order book across providers, sizes the hedge to the book's
depth (so we never move the market — a hedge, not an arb), and prices each factor
as P1 did. When matching is off or finds nothing liquid enough, the factor keeps
a synthetic "engine" book fully hedged — so behaviour is identical to P1 by default.
"""
from __future__ import annotations

from .config import settings
from .discovery import get_discovery
from .matching import get_matcher
from .pricing import clamp_probability
from .risk_engine import FactorAssessment


def assess(shipment_like, cargo_value: float, loading_factor: float,
           discovery=None, matcher=None) -> list[FactorAssessment]:
    if cargo_value <= 0:
        raise ValueError("cargo_value must be positive")
    discovery = discovery or get_discovery()
    matcher = matcher or get_matcher()

    factors = discovery.discover(shipment_like, cargo_value)
    out: list[FactorAssessment] = []
    for f in factors:
        provider = "engine"
        external_id = f"factor-{f.key}"
        question = f.label + "?"
        probability = clamp_probability(f.probability)
        matched = False
        liquidity = 0.0

        if getattr(matcher, "enabled", False):
            hit = matcher.match(f)
            if hit is not None:
                market, provider = hit
                external_id = market["external_id"]
                question = market["question"]
                probability = clamp_probability(market["probability"])
                liquidity = float(market.get("liquidity", 0) or 0)
                matched = True

        covered = f.impact * cargo_value

        # Hedge sizing: a synthetic book is fully hedgeable; a real book is capped
        # at a fraction of its liquidity so the order can be filled without moving
        # the price. The un-hedged remainder (covered - hedge_shares) is retained
        # (basis) risk — this cap is the "hedge, not arbitrage" guardrail.
        if matched and liquidity > 0:
            max_hedgeable = settings.hedge_depth_fraction * liquidity
            hedge_shares = min(covered, max_hedgeable)
        else:
            hedge_shares = covered

        out.append(
            FactorAssessment(
                key=f.key,
                label=f.label,
                category=f.category,
                probability=probability,
                impact=f.impact,
                covered_loss=covered,
                expected_loss=probability * covered,
                hedge_cost=probability * hedge_shares,
                premium=covered * probability * (1.0 + loading_factor),
                market_external_id=external_id,
                market_question=question,
                source=f.source,
                market_provider=provider,
                market_matched=matched,
                hedge_shares=hedge_shares,
                market_liquidity=liquidity,
            )
        )
    return out
