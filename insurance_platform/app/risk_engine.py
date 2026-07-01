"""P1 rule-based risk engine.

Given a shipment, decide which risk factors are relevant and how much of the
cargo value each one puts at risk, then map each factor to a prediction-market
book we can hedge with. This is the deterministic P1 version: a curated factor
template library + keyword-based route selection. P2 will replace factor
DISCOVERY with an LLM while keeping this same output contract.

Output contract: ``analyze(shipment_like, cargo_value, loading)`` returns a list
of ``FactorAssessment``. Both the quote endpoint (no writes) and the buy endpoint
(creates hedge legs) consume the same assessments, so a quote always matches
what gets bought.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import models
from .pricing import clamp_probability


@dataclass(frozen=True)
class FactorTemplate:
    key: str
    label: str
    category: str
    probability: float  # implied probability of the factor materialising
    impact: float       # fraction of cargo value at risk if it does
    # substrings (origin/destination/route/cargo_type, lowercased) that make
    # this factor relevant. Empty tuple => always considered (macro/logistics).
    triggers: tuple = ()


# Curated P1 library. Probabilities/impacts stand in for what a real engine would
# read from live order books + a loss model. Impacts are deliberately modest so a
# basket of 4-6 factors stays economically sensible.
FACTOR_LIBRARY = (
    # --- Route / chokepoint ---
    FactorTemplate(
        "suez_closure", "Suez Canal closed to traffic before deadline",
        models.CAT_ROUTE, 0.12, 0.18,
        ("suez", "red sea", "asia-europe", "europe", "rotterdam", "shanghai", "singapore"),
    ),
    FactorTemplate(
        "red_sea_attacks", "Red Sea shipping attacks disrupt the lane",
        models.CAT_GEOPOLITICAL, 0.30, 0.12,
        ("suez", "red sea", "asia-europe", "europe", "middle east"),
    ),
    FactorTemplate(
        "panama_drought", "Panama Canal draft restrictions cut throughput",
        models.CAT_ROUTE, 0.25, 0.10,
        ("panama", "us east", "gulf", "atlantic", "new york", "houston"),
    ),
    FactorTemplate(
        "port_congestion", "Destination port congestion exceeds threshold",
        models.CAT_ROUTE, 0.28, 0.08,
        (),  # always relevant
    ),
    FactorTemplate(
        "dock_strike", "Dockworkers strike at origin or destination",
        models.CAT_ROUTE, 0.15, 0.09,
        ("us", "usa", "united states", "los angeles", "long beach", "new york", "europe"),
    ),
    # --- Geopolitical ---
    FactorTemplate(
        "new_sanctions", "New sanctions affect the trade route",
        models.CAT_GEOPOLITICAL, 0.18, 0.10,
        ("china", "russia", "iran", "middle east", "shanghai", "asia"),
    ),
    FactorTemplate(
        "tariff_shock", "Sudden tariff/customs change delays clearance",
        models.CAT_GEOPOLITICAL, 0.22, 0.07,
        (),
    ),
    # --- Macro ---
    FactorTemplate(
        "fuel_spike", "Bunker fuel price spikes >15%",
        models.CAT_MACRO, 0.25, 0.05,
        (),
    ),
    FactorTemplate(
        "rate_hike", "Central-bank rate hike raises carry/financing cost",
        models.CAT_MACRO, 0.40, 0.04,
        (),
    ),
    FactorTemplate(
        "fx_swing", "Adverse FX swing erodes cargo value",
        models.CAT_MACRO, 0.35, 0.05,
        (),
    ),
    # --- Weather ---
    FactorTemplate(
        "typhoon", "Typhoon disrupts East-Asia shipping lanes",
        models.CAT_WEATHER, 0.30, 0.08,
        ("asia", "china", "japan", "shanghai", "singapore", "pacific"),
    ),
    FactorTemplate(
        "atlantic_storm", "Atlantic storm season disrupts crossing",
        models.CAT_WEATHER, 0.28, 0.07,
        ("atlantic", "us east", "new york", "europe", "gulf", "houston"),
    ),
)

_MAX_FACTORS = 6


@dataclass
class FactorAssessment:
    key: str
    label: str
    category: str
    probability: float
    impact: float
    covered_loss: float     # impact * cargo_value
    expected_loss: float    # probability * covered_loss
    hedge_cost: float       # probability * covered_loss (buy covered_loss YES shares)
    premium: float          # covered_loss * probability * (1 + loading)
    market_external_id: str
    market_question: str
    source: str = "rule"           # factor discovery source: "rule" | "llm"
    market_provider: str = "engine"  # book source: "engine"(synthetic)|"polymarket"|"kalshi"
    market_matched: bool = False   # True if mapped to a real order book
    hedge_shares: float = 0.0      # YES shares actually hedged (<= covered_loss if depth-capped)
    market_liquidity: float = 0.0  # matched book liquidity (0 for synthetic books)


def _relevant(template, haystack):
    if not template.triggers:
        return True
    return any(t in haystack for t in template.triggers)


def select_factors(shipment_like):
    """Pick relevant factor templates for a shipment.

    ``shipment_like`` needs ``origin``, ``destination`` and optionally ``route``
    / ``cargo_type`` attributes (a Shipment ORM row or any object/namespace).
    """
    parts = [
        getattr(shipment_like, "origin", "") or "",
        getattr(shipment_like, "destination", "") or "",
        getattr(shipment_like, "route", "") or "",
        getattr(shipment_like, "cargo_type", "") or "",
    ]
    haystack = " ".join(parts).lower()
    chosen = [t for t in FACTOR_LIBRARY if _relevant(t, haystack)]
    # Rank by expected severity (probability * impact) so the most material
    # factors survive the cap.
    chosen.sort(key=lambda t: t.probability * t.impact, reverse=True)
    return chosen[:_MAX_FACTORS]


def analyze(shipment_like, cargo_value, loading_factor):
    """Turn a shipment into a hedged, priced list of factor assessments."""
    if cargo_value <= 0:
        raise ValueError("cargo_value must be positive")
    out = []
    for t in select_factors(shipment_like):
        p = clamp_probability(t.probability)
        covered = t.impact * cargo_value
        expected = p * covered
        premium = covered * p * (1.0 + loading_factor)
        out.append(
            FactorAssessment(
                key=t.key,
                label=t.label,
                category=t.category,
                probability=p,
                impact=t.impact,
                covered_loss=covered,
                expected_loss=expected,
                hedge_cost=p * covered,
                premium=premium,
                market_external_id="factor-" + t.key,
                market_question=t.label + "?",
                hedge_shares=covered,
            )
        )
    return out
