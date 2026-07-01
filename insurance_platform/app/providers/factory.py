"""Provider selection + transparent fallback to the mock provider."""
from __future__ import annotations

import logging

from ..config import settings
from .base import MarketData, MarketProvider
from .mock import MockProvider
from .polymarket import PolymarketGammaProvider

logger = logging.getLogger(__name__)


class FallbackProvider(MarketProvider):
    """Delegate to ``primary``; on ANY error fall back to ``backup``.

    This is what makes the platform run under the sandbox's egress block and any
    real-world API outage: a network error, timeout, non-200, or parse failure
    silently degrades to seeded mock data (logged as a warning).
    """

    name = "fallback"

    def __init__(self, primary: MarketProvider, backup: MarketProvider) -> None:
        self.primary = primary
        self.backup = backup
        self.name = f"{primary.name}->{backup.name}"

    def _try(self, method: str, *args):
        try:
            result = getattr(self.primary, method)(*args)
            # list_markets returning empty is treated as a soft failure so the
            # UI is never blank when the API is up but filtered everything out.
            if method == "list_markets" and not result:
                raise RuntimeError("primary returned no markets")
            return result
        except Exception as exc:  # noqa: BLE001 - deliberate broad fallback
            logger.warning(
                "provider %s.%s failed (%s); using %s",
                self.primary.name,
                method,
                exc,
                self.backup.name,
            )
            return getattr(self.backup, method)(*args)

    def list_markets(self, limit: int = 50) -> list[MarketData]:
        return self._try("list_markets", limit)

    def get_market(self, external_id: str) -> MarketData | None:
        return self._try("get_market", external_id)

    def get_resolution(self, external_id: str) -> str | None:
        return self._try("get_resolution", external_id)


def get_provider() -> MarketProvider:
    if settings.provider == "mock":
        return MockProvider()
    # Default: real Polymarket API, auto-falling back to mock.
    return FallbackProvider(
        PolymarketGammaProvider(timeout=settings.http_timeout), MockProvider()
    )
