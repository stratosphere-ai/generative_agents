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
    def __init__(self, providers: list[MarketProvider], min_score: float,
                 min_liquidity: float = 0.0) -> None:
        self.providers = providers
        self.min_score = min_score
        self.min_liquidity = min_liquidity
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
        """Pick the most *liquid* relevant book (hot-book selection).

        Keep only candidates that clear both the relevance floor (keyword score)
        and the liquidity floor, then prefer the deepest book so the hedge can
        actually be filled; break ties by relevance. This is what guarantees we
        bet on liquid markets rather than the closest-worded thin one.
        """
        query = getattr(factor, "search_query", None) or getattr(factor, "label", "")
        eligible = [
            c for c in self._candidates(query)
            if c.score >= self.min_score and c.liquidity >= self.min_liquidity
        ]
        if not eligible:
            return None
        best = max(eligible, key=lambda c: (c.liquidity, c.score))
        return best.market, best.provider


_PROVIDER_BUILDERS = {
    "polymarket": lambda: PolymarketGammaProvider(timeout=settings.http_timeout),
    "kalshi": lambda: KalshiProvider(timeout=settings.http_timeout),
}


def get_matcher() -> MarketMatcher:
    """Build the matcher from config; disabled (no providers) when INSURE_MATCH=off."""
    if settings.match != "on":
        return MarketMatcher([], settings.match_min_score, settings.match_min_liquidity)
    providers = []
    for name in settings.match_providers.split(","):
        name = name.strip().lower()
        builder = _PROVIDER_BUILDERS.get(name)
        if builder:
            providers.append(builder())
    return MarketMatcher(providers, settings.match_min_score, settings.match_min_liquidity)
