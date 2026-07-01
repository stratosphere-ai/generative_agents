"""Provider interface shared by every market data source."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, TypedDict


class MarketData(TypedDict):
    external_id: str
    question: str
    description: str | None
    probability: float  # YES-implied probability, in (0, 1)
    end_date: str | None  # ISO8601 or None
    status: str  # "open" | "resolved_yes" | "resolved_no" | "closed"
    raw: dict[str, Any]


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
