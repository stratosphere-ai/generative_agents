"""Provider interface shared by every market data source."""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, TypedDict


class MarketData(TypedDict):
    external_id: str
    question: str
    description: str | None
    probability: float  # YES-implied probability, in (0, 1)
    end_date: str | None  # ISO8601 or None
    status: str  # "open" | "resolved_yes" | "resolved_no" | "closed"
    raw: dict[str, Any]


_STOP = {
    "will", "the", "a", "an", "to", "of", "in", "on", "for", "before", "by", "at",
    "and", "or", "be", "is", "are", "during", "than", "more", "less", "that", "this",
}


def _tokenize(text: str) -> set[str]:
    return {
        w for w in re.findall(r"[a-z0-9]+", (text or "").lower())
        if len(w) > 2 and w not in _STOP
    }


def _overlap_score(query_tokens: set[str], candidate: str) -> float:
    cand = _tokenize(candidate)
    if not cand or not query_tokens:
        return 0.0
    inter = query_tokens & cand
    # Jaccard-ish: overlap relative to the (smaller) query set.
    return len(inter) / len(query_tokens)


@dataclass
class ScoredMarket:
    market: "MarketData"
    score: float
    provider: str


class MarketProvider(ABC):
    name: str = "base"

    @abstractmethod
    def list_markets(self, limit: int = 50) -> list[MarketData]:
        """Return currently insurable binary (Yes/No) markets."""

    @abstractmethod
    def get_market(self, external_id: str) -> MarketData | None:
        """Return a single market by its provider id, or None."""

    @abstractmethod
    def get_resolution(self, external_id: str) -> str | None:
        """Return 'resolved_yes' | 'resolved_no', or None if unresolved."""

    def search_markets(self, query: str, limit: int = 40) -> list["ScoredMarket"]:
        """Return open markets ranked by keyword overlap with ``query``.

        Default implementation: fetch a batch via list_markets and score locally
        by token overlap. Providers with native search may override.
        """
        query_tokens = _tokenize(query)
        if not query_tokens:
            return []
        scored: list[ScoredMarket] = []
        for md in self.list_markets(limit=limit):
            if md.get("status") != "open":
                continue
            score = _overlap_score(query_tokens, md.get("question", ""))
            if score > 0:
                scored.append(ScoredMarket(market=md, score=score, provider=self.name))
        scored.sort(key=lambda s: s.score, reverse=True)
        return scored
