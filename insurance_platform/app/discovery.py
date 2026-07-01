"""Risk-factor discovery abstraction: rule-based (P1) or LLM-based (P2).

Both produce a uniform list of ``DiscoveredFactor``. ``FallbackDiscovery`` wraps a
primary strategy so any failure (or empty result) degrades to the deterministic
rule engine — the app therefore runs identically offline / without an API key.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass

from . import risk_engine
from .config import settings
from .pricing import clamp_probability

logger = logging.getLogger(__name__)

_VALID_CATEGORIES = {
    risk_engine.models.CAT_ROUTE,
    risk_engine.models.CAT_GEOPOLITICAL,
    risk_engine.models.CAT_MACRO,
    risk_engine.models.CAT_WEATHER,
}


@dataclass
class DiscoveredFactor:
    key: str
    label: str
    category: str
    probability: float
    impact: float
    search_query: str
    source: str  # "rule" | "llm"


class FactorDiscovery(ABC):
    name = "base"

    @abstractmethod
    def discover(self, shipment_like, cargo_value: float) -> list[DiscoveredFactor]:
        ...


class RuleDiscovery(FactorDiscovery):
    """Deterministic P1 engine: keyword-selected factor templates."""

    name = "rule"

    def discover(self, shipment_like, cargo_value: float) -> list[DiscoveredFactor]:
        out = []
        for t in risk_engine.select_factors(shipment_like):
            out.append(
                DiscoveredFactor(
                    key=t.key,
                    label=t.label,
                    category=t.category,
                    probability=clamp_probability(t.probability),
                    impact=t.impact,
                    search_query=t.label,
                    source="rule",
                )
            )
        return out


def _clamp_impact(v: float) -> float:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return 0.05
    return max(0.01, min(0.5, v))


class LLMDiscovery(FactorDiscovery):
    """Claude proposes the factors. Raises on any failure (caught by fallback)."""

    name = "llm"

    def discover(self, shipment_like, cargo_value: float) -> list[DiscoveredFactor]:
        from .llm import discover_factors_llm

        raw = discover_factors_llm(shipment_like, cargo_value)
        out = []
        for i, f in enumerate(raw):
            category = f.get("category")
            if category not in _VALID_CATEGORIES:
                category = risk_engine.models.CAT_MACRO
            key = str(f.get("key") or f"llm_factor_{i}").strip().lower().replace(" ", "_")
            label = str(f.get("label") or key)
            out.append(
                DiscoveredFactor(
                    key=key,
                    label=label,
                    category=category,
                    probability=clamp_probability(float(f.get("probability", 0.2))),
                    impact=_clamp_impact(f.get("impact", 0.05)),
                    search_query=str(f.get("search_query") or label),
                    source="llm",
                )
            )
        if not out:
            raise ValueError("LLM discovery produced no usable factors")
        return out


class FallbackDiscovery(FactorDiscovery):
    """Try ``primary``; on ANY error or empty result use ``backup``."""

    def __init__(self, primary: FactorDiscovery, backup: FactorDiscovery) -> None:
        self.primary = primary
        self.backup = backup
        self.name = f"{primary.name}->{backup.name}"

    def discover(self, shipment_like, cargo_value: float) -> list[DiscoveredFactor]:
        try:
            result = self.primary.discover(shipment_like, cargo_value)
            if result:
                return result
            raise RuntimeError("primary discovery returned no factors")
        except Exception as exc:  # noqa: BLE001 - deliberate broad fallback
            logger.warning(
                "discovery %s failed (%s); falling back to %s",
                self.primary.name, exc, self.backup.name,
            )
            return self.backup.discover(shipment_like, cargo_value)


def get_discovery() -> FactorDiscovery:
    if settings.discovery == "llm":
        return FallbackDiscovery(LLMDiscovery(), RuleDiscovery())
    return RuleDiscovery()
