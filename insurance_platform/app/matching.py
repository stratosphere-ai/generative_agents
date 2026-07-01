"""Match a discovered risk factor to a real prediction-market order book.

Queries every configured provider (Polymarket, Kalshi), aggregates the scored
candidates, and returns the single best market above a score threshold — or None
if nothing matches, in which case the engine synthesises a book for that factor.

Every provider call is wrapped: a blocked or failing provider (e.g. this
sandbox's egress block) contributes zero candidates rather than raising, so
matching degrades gracefully to synthetic books.
"""
from __future__ import annotations
from typing import Optional

import logging

from .config import settings
from .providers.base import MarketData, MarketProvider, ScoredMarket
from .providers.kalshi import KalshiProvider
from .providers.polymarket import PolymarketGammaProvider

logger = logging.getLogger(__name__)


class MarketMatcher:
    def __init__(self, providers: list[MarketProvider], min_score: float) -> None:
        self.providers = providers
        self.min_score = min_score
        self.enabled = bool(providers)

    def _candidates(self, query: str) -> list[ScoredMarket]:
        cands: list[ScoredMarket] = []
        for p in self.providers:
            try:
                cands.extend(p.search_markets(query))
            except Exception as exc:  # noqa: BLE001 - provider may be unreachable
                logger.warning("provider %s search failed (%s)", p.name, exc)
        return cands

    def match(self, factor) -> Optional[tuple[MarketData, str]]:
        """Return (market, provider_name) for the best match, or None."""
        query = getattr(factor, "search_query", None) or getattr(factor, "label", "")
        best = None
        for cand in self._candidates(query):
            if cand.score >= self.min_score and (best is None or cand.score > best.score):
                best = cand
        if best is None:
            return None
        return best.market, best.provider


_PROVIDER_BUILDERS = {
    "polymarket": lambda: PolymarketGammaProvider(timeout=settings.http_timeout),
    "kalshi": lambda: KalshiProvider(timeout=settings.http_timeout),
}


def get_matcher() -> MarketMatcher:
    """Build the matcher from config; disabled (no providers) when INSURE_MATCH=off."""
    if settings.match != "on":
        return MarketMatcher([], settings.match_min_score)
    providers = []
    for name in settings.match_providers.split(","):
        name = name.strip().lower()
        builder = _PROVIDER_BUILDERS.get(name)
        if builder:
            providers.append(builder())
    return MarketMatcher(providers, settings.match_min_score)
