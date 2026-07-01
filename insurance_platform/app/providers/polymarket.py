"""Polymarket Gamma API provider (public, read-only, no API key).

Endpoint: https://gamma-api.polymarket.com/markets

Gamma returns several fields as JSON-encoded *strings* (e.g. outcomes and
outcomePrices are strings like '["Yes","No"]' / '["0.63","0.37"]'), so all
parsing here is defensive. Only binary Yes/No markets are surfaced, since the
insurance model assumes a single YES-implied probability.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from ..pricing import clamp_probability
from .base import MarketData, MarketProvider

logger = logging.getLogger(__name__)

GAMMA_URL = "https://gamma-api.polymarket.com/markets"


def _loads_list(value: Any) -> list:
    """Parse a field that may be a JSON-encoded string or already a list."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except (ValueError, TypeError):
            return []
    return []


def _yes_index(outcomes: list) -> int | None:
    for i, o in enumerate(outcomes):
        if str(o).strip().lower() == "yes":
            return i
    return None


def _extract_probability(raw: dict[str, Any]) -> float | None:
    """YES-implied probability with a fallback chain."""
    outcomes = _loads_list(raw.get("outcomes"))
    prices = _loads_list(raw.get("outcomePrices"))
    yi = _yes_index(outcomes)
    if yi is not None and yi < len(prices):
        try:
            return clamp_probability(float(prices[yi]))
        except (ValueError, TypeError):
            pass
    # Fallbacks.
    for key in ("lastTradePrice", "bestBid"):
        val = raw.get(key)
        if val is not None:
            try:
                return clamp_probability(float(val))
            except (ValueError, TypeError):
                continue
    bid, ask = raw.get("bestBid"), raw.get("bestAsk")
    if bid is not None and ask is not None:
        try:
            return clamp_probability((float(bid) + float(ask)) / 2.0)
        except (ValueError, TypeError):
            pass
    return None


def _is_binary(raw: dict[str, Any]) -> bool:
    outcomes = [str(o).strip().lower() for o in _loads_list(raw.get("outcomes"))]
    return len(outcomes) == 2 and set(outcomes) == {"yes", "no"}


def _status(raw: dict[str, Any], probability: float) -> str:
    if raw.get("closed"):
        # Collapsed price near 1/0 signals the resolved outcome.
        if probability >= 0.99:
            return "resolved_yes"
        if probability <= 0.01:
            return "resolved_no"
        return "closed"
    if raw.get("active", True):
        return "open"
    return "closed"


def _to_market_data(raw: dict[str, Any]) -> MarketData | None:
    if not _is_binary(raw):
        return None
    prob = _extract_probability(raw)
    if prob is None:
        return None
    return MarketData(
        external_id=str(raw.get("id")),
        question=raw.get("question") or raw.get("title") or "(untitled market)",
        description=raw.get("description"),
        probability=prob,
        end_date=raw.get("endDate"),
        status=_status(raw, prob),
        raw=raw,
    )


class PolymarketGammaProvider(MarketProvider):
    name = "polymarket"

    def __init__(self, timeout: float = 6.0) -> None:
        self._timeout = timeout

    def _get(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        resp = httpx.get(GAMMA_URL, params=params, timeout=self._timeout)
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else data.get("data", [])

    def list_markets(self, limit: int = 50) -> list[MarketData]:
        # Over-fetch because the binary filter drops many multi-outcome markets.
        rows = self._get(
            {
                "closed": "false",
                "active": "true",
                "limit": max(limit * 4, limit),
                "order": "volume",
                "ascending": "false",
            }
        )
        out: list[MarketData] = []
        for raw in rows:
            md = _to_market_data(raw)
            if md is not None and md["status"] == "open":
                out.append(md)
            if len(out) >= limit:
                break
        return out

    def get_market(self, external_id: str) -> MarketData | None:
        rows = self._get({"id": external_id})
        for raw in rows:
            md = _to_market_data(raw)
            if md is not None:
                return md
        return None

    def get_resolution(self, external_id: str) -> str | None:
        md = self.get_market(external_id)
        if md is None:
            return None
        if md["status"] in ("resolved_yes", "resolved_no"):
            return md["status"]
        return None
