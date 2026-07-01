"""Kalshi provider (public market data, read-only).

Endpoint: https://api.elections.kalshi.com/trade-api/v2/markets

Kalshi quotes YES in cents (yes_bid/yes_ask, 1-99). Probability = mid/100.
Only binary markets are surfaced. Parsing is defensive (fields vary by market
type / status), mirroring the Polymarket provider. Reachability is not required
here — the matcher wraps provider calls, so a blocked Kalshi simply yields no
candidates and the factor falls back to a synthetic book.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from ..pricing import clamp_probability
from .base import MarketData, MarketProvider

logger = logging.getLogger(__name__)

KALSHI_URL = "https://api.elections.kalshi.com/trade-api/v2/markets"


def _probability(raw: dict[str, Any]) -> float | None:
    bid, ask = raw.get("yes_bid"), raw.get("yes_ask")
    try:
        if bid is not None and ask is not None:
            return clamp_probability((float(bid) + float(ask)) / 200.0)
    except (ValueError, TypeError):
        pass
    for key in ("last_price", "yes_ask", "yes_bid"):
        val = raw.get(key)
        if val is not None:
            try:
                return clamp_probability(float(val) / 100.0)
            except (ValueError, TypeError):
                continue
    return None


def _status(raw: dict[str, Any], probability: float) -> str:
    st = str(raw.get("status", "")).lower()
    result = str(raw.get("result", "")).lower()
    if st in ("finalized", "settled", "closed"):
        if result == "yes" or probability >= 0.99:
            return "resolved_yes"
        if result == "no" or probability <= 0.01:
            return "resolved_no"
        return "closed"
    if st in ("active", "open", ""):
        return "open"
    return "closed"


def _to_market_data(raw: dict[str, Any]) -> MarketData | None:
    prob = _probability(raw)
    if prob is None:
        return None
    ticker = raw.get("ticker") or raw.get("id")
    if not ticker:
        return None
    question = raw.get("title") or raw.get("subtitle") or str(ticker)
    return MarketData(
        external_id=str(ticker),
        question=question,
        description=raw.get("subtitle"),
        probability=prob,
        end_date=raw.get("close_time") or raw.get("expiration_time"),
        status=_status(raw, prob),
        raw=raw,
    )


class KalshiProvider(MarketProvider):
    name = "kalshi"

    def __init__(self, timeout: float = 6.0) -> None:
        self._timeout = timeout

    def _get(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        resp = httpx.get(KALSHI_URL, params=params, timeout=self._timeout)
        resp.raise_for_status()
        data = resp.json()
        return data.get("markets", []) if isinstance(data, dict) else data

    def list_markets(self, limit: int = 50) -> list[MarketData]:
        rows = self._get({"limit": max(limit, 100), "status": "open"})
        out: list[MarketData] = []
        for raw in rows:
            md = _to_market_data(raw)
            if md is not None and md["status"] == "open":
                out.append(md)
            if len(out) >= limit:
                break
        return out

    def get_market(self, external_id: str) -> MarketData | None:
        try:
            resp = httpx.get(f"{KALSHI_URL}/{external_id}", timeout=self._timeout)
            resp.raise_for_status()
            raw = resp.json().get("market", {})
        except Exception:  # noqa: BLE001
            return None
        return _to_market_data(raw)

    def get_resolution(self, external_id: str) -> str | None:
        md = self.get_market(external_id)
        if md is None:
            return None
        return md["status"] if md["status"] in ("resolved_yes", "resolved_no") else None
