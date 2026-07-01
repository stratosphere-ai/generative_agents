from types import SimpleNamespace

import pytest

from app import engine, risk_engine
from app.discovery import RuleDiscovery
from app.matching import MarketMatcher
from app.providers.base import MarketData


def _ship():
    return SimpleNamespace(origin="CityA", destination="CityB", route=None, cargo_type=None)


def test_assess_rule_matchoff_reproduces_p1_economics():
    disc = RuleDiscovery()
    matcher = MarketMatcher([], min_score=0.34)  # disabled
    got = engine.assess(_ship(), 100000, 0.15, discovery=disc, matcher=matcher)
    p1 = risk_engine.analyze(_ship(), 100000, 0.15)
    assert {a.key for a in got} == {a.key for a in p1}
    got_by = {a.key: a for a in got}
    for a in p1:
        g = got_by[a.key]
        assert g.premium == pytest.approx(a.premium)
        assert g.covered_loss == pytest.approx(a.covered_loss)
        assert g.market_provider == "engine"
        assert g.market_matched is False
        assert g.source == "rule"


class _AlwaysMatch(MarketMatcher):
    def __init__(self):
        super().__init__(providers=["x"], min_score=0.0)  # enabled
    def match(self, factor):
        md = MarketData(external_id="poly-123", question="Real book?", description=None,
                        probability=0.5, end_date=None, status="open",
                        liquidity=10000.0, raw={})
        return md, "polymarket"


def test_assess_uses_real_book_when_matched():
    got = engine.assess(_ship(), 100000, 0.15,
                        discovery=RuleDiscovery(), matcher=_AlwaysMatch())
    assert got
    for a in got:
        assert a.market_matched is True
        assert a.market_provider == "polymarket"
        assert a.market_external_id == "poly-123"
        assert a.probability == pytest.approx(0.5)  # real book's price drives pricing
        # premium reprices at the real probability
        assert a.premium == pytest.approx(a.covered_loss * 0.5 * 1.15)


def test_assess_caps_hedge_to_book_depth():
    # Matched book liquidity=10000, default depth fraction 0.10 -> max hedge 1000.
    got = engine.assess(_ship(), 100000, 0.15,
                        discovery=RuleDiscovery(), matcher=_AlwaysMatch())
    assert got
    for a in got:
        assert a.market_matched is True
        assert a.market_liquidity == 10000.0
        assert a.hedge_shares <= 1000.0 + 1e-6          # capped to depth fraction
        if a.covered_loss > 1000.0:
            assert a.hedge_shares == pytest.approx(1000.0)  # depth-limited
        # hedge cost prices the *hedged* shares, not the full covered loss
        assert a.hedge_cost == pytest.approx(a.probability * a.hedge_shares)
