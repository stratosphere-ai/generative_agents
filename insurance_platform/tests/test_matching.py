from types import SimpleNamespace

from app.matching import MarketMatcher
from app.providers.base import MarketData, MarketProvider


def _md(external_id, question, prob=0.3, liquidity=0.0):
    return MarketData(external_id=external_id, question=question, description=None,
                      probability=prob, end_date=None, status="open",
                      liquidity=liquidity, raw={})


class _FakeProvider(MarketProvider):
    def __init__(self, name, markets):
        self.name = name
        self._markets = markets
    def list_markets(self, limit=50):
        return self._markets
    def get_market(self, external_id):
        return None
    def get_resolution(self, external_id):
        return None


class _BoomProvider(MarketProvider):
    name = "boom"
    def list_markets(self, limit=50):
        raise RuntimeError("network blocked")
    def get_market(self, external_id):
        raise RuntimeError("network blocked")
    def get_resolution(self, external_id):
        raise RuntimeError("network blocked")


def _factor(label, query):
    return SimpleNamespace(label=label, search_query=query)


def test_match_picks_best_above_threshold():
    poly = _FakeProvider("polymarket", [
        _md("p1", "Will the Suez Canal be closed to shipping traffic?", 0.22),
        _md("p2", "Will Bitcoin hit 100k?", 0.4),
    ])
    m = MarketMatcher([poly], min_score=0.34)
    hit = m.match(_factor("Suez Canal closed", "suez canal closed shipping traffic"))
    assert hit is not None
    market, provider = hit
    assert market["external_id"] == "p1"
    assert provider == "polymarket"


def test_match_returns_none_below_threshold():
    poly = _FakeProvider("polymarket", [_md("p2", "Will Bitcoin hit 100k?", 0.4)])
    m = MarketMatcher([poly], min_score=0.34)
    assert m.match(_factor("Suez Canal closed", "suez canal closed shipping")) is None


def test_match_aggregates_across_providers_and_swallows_errors():
    poly = _BoomProvider()
    kalshi = _FakeProvider("kalshi", [
        _md("k1", "Will a typhoon disrupt East Asia shipping lanes?", 0.31),
    ])
    m = MarketMatcher([poly, kalshi], min_score=0.34)
    hit = m.match(_factor("Typhoon disrupts East-Asia shipping lanes",
                          "typhoon disrupt east asia shipping lanes"))
    assert hit is not None and hit[1] == "kalshi"


def test_disabled_matcher_has_no_providers():
    m = MarketMatcher([], min_score=0.34)
    assert m.enabled is False


def test_match_prefers_most_liquid_relevant_book():
    poly = _FakeProvider("polymarket", [
        _md("thin", "Will the Suez Canal be closed to shipping traffic?", 0.2, liquidity=6000),
        _md("deep", "Will the Suez Canal close to shipping traffic this quarter?", 0.25, liquidity=90000),
    ])
    m = MarketMatcher([poly], min_score=0.34, min_liquidity=5000)
    market, provider = m.match(_factor("Suez Canal closed", "suez canal closed shipping traffic"))
    assert market["external_id"] == "deep"   # most liquid wins, not best keyword score


def test_match_liquidity_floor_skips_thin_book():
    poly = _FakeProvider("polymarket", [
        _md("thin", "Will the Suez Canal be closed to shipping traffic?", 0.2, liquidity=100),
    ])
    m = MarketMatcher([poly], min_score=0.34, min_liquidity=5000)
    assert m.match(_factor("Suez Canal closed", "suez canal closed shipping traffic")) is None
