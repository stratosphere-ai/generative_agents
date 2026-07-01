"""Runtime configuration, read from environment variables with sane defaults.

Everything is overridable via env so the same code runs in this (network-blocked)
sandbox and in a deployment that can reach Polymarket.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
STATIC_DIR = BASE_DIR / "static"


def _get_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Settings:
    # "polymarket" (real API, auto-falls back to mock on any error) or "mock".
    provider: str = os.environ.get("INSURE_PROVIDER", "polymarket").lower()
    # Insurer margin/fee added on top of the fair (market-implied) premium.
    loading_factor: float = _get_float("INSURE_LOADING_FACTOR", 0.15)
    # Minimum cash / reserved-liabilities ratio required to underwrite a policy.
    min_solvency: float = _get_float("INSURE_MIN_SOLVENCY", 1.0)
    # Cash the pool is seeded with on first run.
    seed_capital: float = _get_float("INSURE_SEED_CAPITAL", 2_000_000.0)
    # SQLAlchemy URL. Defaults to a file DB under data/.
    database_url: str = os.environ.get(
        "INSURE_DATABASE_URL", f"sqlite:///{DATA_DIR / 'insurance.db'}"
    )
    # HTTP timeout (seconds) for the real market provider.
    http_timeout: float = _get_float("INSURE_HTTP_TIMEOUT", 6.0)

    # --- P2: factor discovery + real order-book matching ---
    # Factor discovery: "rule" (deterministic templates) or "llm" (Claude, auto-
    # falls back to rule on any error).
    discovery: str = os.environ.get("INSURE_DISCOVERY", "rule").lower()
    # Model used for LLM discovery.
    llm_model: str = os.environ.get("INSURE_LLM_MODEL", "claude-opus-4-8")
    # Real order-book matching: "off" (synthesize engine books) or "on" (match
    # against real Polymarket/Kalshi books; falls back to synthetic per factor).
    match: str = os.environ.get("INSURE_MATCH", "off").lower()
    match_providers: str = os.environ.get("INSURE_MATCH_PROVIDERS", "polymarket,kalshi")
    match_min_score: float = _get_float("INSURE_MATCH_MIN_SCORE", 0.34)


settings = Settings()
