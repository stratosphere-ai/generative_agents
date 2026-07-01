"""P2 orchestration: discover factors -> match to real books -> price the basket.

This is the seam that unifies P1 and P2. ``assess`` runs the configured discovery
strategy (rule or LLM+fallback), optionally maps each factor to a real
prediction-market order book across providers, and prices each factor exactly
as P1 did. When matching is off or finds nothing, the factor keeps a synthetic
"engine" book — so behaviour is identical to P1 by default.
"""
from __future__ import annotations

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

        if getattr(matcher, "enabled", False):
            hit = matcher.match(f)
            if hit is not None:
                market, provider = hit
                external_id = market["external_id"]
                question = market["question"]
                probability = clamp_probability(market["probability"])
                matched = True

        covered = f.impact * cargo_value
        out.append(
            FactorAssessment(
                key=f.key,
                label=f.label,
                category=f.category,
                probability=probability,
                impact=f.impact,
                covered_loss=covered,
                expected_loss=probability * covered,
                hedge_cost=probability * covered,
                premium=covered * probability * (1.0 + loading_factor),
                market_external_id=external_id,
                market_question=question,
                source=f.source,
                market_provider=provider,
                market_matched=matched,
            )
        )
    return out
