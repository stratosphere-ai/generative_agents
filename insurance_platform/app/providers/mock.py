"""Deterministic in-memory provider.

Used as the automatic fallback whenever the real API is unreachable (as in this
network-blocked sandbox) and as the seed data for offline demos/tests. Includes
one pre-resolvable market so the payout flow can be demonstrated end-to-end.
"""
from __future__ import annotations

from .base import MarketData, MarketProvider

_SEED: list[MarketData] = [
    {
        "external_id": "mock-hurricane-fl",
        "question": "Will a Category 3+ hurricane make landfall in Florida in Q3 2026?",
        "description": "Insure against a major hurricane landfall. Backed by the storm-risk prediction market.",
        "probability": 0.28,
        "end_date": "2026-09-30T23:59:59Z",
        "status": "open",
        "raw": {},
    },
    {
        "external_id": "mock-flight-delay",
        "question": "Will >20% of US domestic flights be delayed on 2026-07-04?",
        "description": "Travel-disruption cover for the July 4th holiday peak.",
        "probability": 0.42,
        "end_date": "2026-07-05T00:00:00Z",
        "status": "open",
        "raw": {},
    },
    {
        "external_id": "mock-btc-crash",
        "question": "Will BTC fall more than 20% in July 2026?",
        "description": "Portfolio drawdown protection tied to the crypto-crash market.",
        "probability": 0.18,
        "end_date": "2026-07-31T23:59:59Z",
        "status": "open",
        "raw": {},
    },
    {
        "external_id": "mock-heatwave",
        "question": "Will a national heat emergency be declared in August 2026?",
        "description": "Parametric heat-event cover for outdoor operators.",
        "probability": 0.35,
        "end_date": "2026-08-31T23:59:59Z",
        "status": "open",
        "raw": {},
    },
    {
        "external_id": "mock-rain-marathon",
        "question": "Will it rain during the city marathon on 2026-07-12?",
        "description": "Event-day weather cover for organizers.",
        "probability": 0.55,
        "end_date": "2026-07-12T18:00:00Z",
        "status": "open",
        "raw": {},
    },
    {
        "external_id": "mock-demo-resolvable",
        "question": "[DEMO] Will the sample event resolve YES? (use admin to resolve)",
        "description": "A demo market for exercising resolution + claim payout.",
        "probability": 0.50,
        "end_date": "2026-07-15T00:00:00Z",
        "status": "open",
        "raw": {},
    },
]


class MockProvider(MarketProvider):
    name = "mock"

    def __init__(self) -> None:
        self._by_id = {m["external_id"]: m for m in _SEED}

    def list_markets(self, limit: int = 50) -> list[MarketData]:
        return [dict(m) for m in _SEED[:limit]]  # type: ignore[misc]

    def get_market(self, external_id: str) -> MarketData | None:
        m = self._by_id.get(external_id)
        return dict(m) if m else None  # type: ignore[return-value]

    def get_resolution(self, external_id: str) -> str | None:
        # Mock markets are resolved manually via the admin endpoint.
        return None
