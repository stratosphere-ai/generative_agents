from app.providers.base import MarketProvider
from app.providers.factory import FallbackProvider
from app.providers.mock import MockProvider
from app.providers.polymarket import _to_market_data

# A trimmed Gamma-shaped market object. Note the JSON-*string* array fields.
GAMMA_FIXTURE = {
    "id": "0x123",
    "question": "Will event X happen?",
    "description": "desc",
    "outcomes": '["Yes", "No"]',
    "outcomePrices": '["0.63", "0.37"]',
    "endDate": "2026-12-31T00:00:00Z",
    "active": True,
    "closed": False,
}


def test_mock_provider_shape():
    p = MockProvider()
    markets = p.list_markets()
    assert markets
    for m in markets:
        assert 0 < m["probability"] < 1
        assert m["external_id"]
        assert m["status"] == "open"


def test_polymarket_mapping_parses_json_string_fields():
    md = _to_market_data(GAMMA_FIXTURE)
    assert md is not None
    assert md["external_id"] == "0x123"
    assert md["question"] == "Will event X happen?"
    assert abs(md["probability"] - 0.63) < 1e-9
    assert md["status"] == "open"


def test_polymarket_filters_non_binary():
    multi = dict(GAMMA_FIXTURE)
    multi["outcomes"] = '["A", "B", "C"]'
    multi["outcomePrices"] = '["0.3", "0.3", "0.4"]'
    assert _to_market_data(multi) is None


def test_polymarket_resolved_status_from_collapsed_price():
    closed_yes = dict(GAMMA_FIXTURE)
    closed_yes["closed"] = True
    closed_yes["outcomePrices"] = '["1.0", "0.0"]'
    md = _to_market_data(closed_yes)
    assert md["status"] == "resolved_yes"


class _Boom(MarketProvider):
    name = "boom"

    def list_markets(self, limit=50):
        raise RuntimeError("network down")

    def get_market(self, external_id):
        raise RuntimeError("network down")

    def get_resolution(self, external_id):
        raise RuntimeError("network down")


def test_fallback_uses_backup_when_primary_fails():
    fb = FallbackProvider(_Boom(), MockProvider())
    markets = fb.list_markets()
    assert markets  # came from the mock backup
    assert markets[0]["external_id"].startswith("mock-")
