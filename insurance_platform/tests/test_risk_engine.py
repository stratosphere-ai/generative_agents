from types import SimpleNamespace

import pytest

from app import risk_engine


def _ship(origin="CityA", destination="CityB", route=None, cargo_type=None):
    return SimpleNamespace(origin=origin, destination=destination, route=route, cargo_type=cargo_type)


def test_always_on_factors_selected_for_generic_route():
    factors = risk_engine.select_factors(_ship())
    keys = {t.key for t in factors}
    # Macro/logistics factors have no triggers -> always considered.
    assert {"port_congestion", "tariff_shock", "fuel_spike", "rate_hike", "fx_swing"} <= keys


def test_route_keyword_triggers_specific_factor():
    keys = {t.key for t in risk_engine.select_factors(_ship(destination="Rotterdam (Europe)"))}
    assert "suez_closure" in keys or "red_sea_attacks" in keys


def test_factor_count_capped():
    factors = risk_engine.select_factors(_ship(destination="Suez Red Sea Asia-Europe Shanghai"))
    assert len(factors) <= risk_engine._MAX_FACTORS


def test_analyze_math():
    ass = risk_engine.analyze(_ship(), cargo_value=100000, loading_factor=0.15)
    assert ass
    for a in ass:
        assert 0 < a.probability < 1
        assert 0 < a.impact <= 1
        assert a.covered_loss == pytest.approx(a.impact * 100000)
        assert a.expected_loss == pytest.approx(a.probability * a.covered_loss)
        assert a.premium == pytest.approx(a.covered_loss * a.probability * 1.15)
        assert a.market_external_id.startswith("factor-")


def test_analyze_rejects_bad_value():
    with pytest.raises(ValueError):
        risk_engine.analyze(_ship(), cargo_value=0, loading_factor=0.15)
