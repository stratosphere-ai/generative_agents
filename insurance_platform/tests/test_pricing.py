import math

import pytest

from app import pricing


def test_premium_formula():
    # C=1000, p=0.4, loading=0.15 -> 1000*0.4*1.15 = 460
    assert pricing.calc_premium(1000, 0.40, 0.15) == pytest.approx(460.0)


def test_premium_clamps_extreme_probabilities():
    # p=0 -> clamped to floor, premium tiny but > 0
    low = pricing.calc_premium(1000, 0.0, 0.0)
    assert 0 < low <= 1000 * pricing.PROB_FLOOR + 1e-6
    # p=1 -> clamped to ceil, below full coverage
    high = pricing.calc_premium(1000, 1.0, 0.0)
    assert high < 1000
    assert high == pytest.approx(1000 * pricing.PROB_CEIL)


def test_calc_reserve_equals_coverage():
    assert pricing.calc_reserve(2500) == 2500


def test_hedge_cost():
    assert pricing.calc_hedge_cost(1000, 0.40) == pytest.approx(400.0)


def test_invalid_coverage_raises():
    for fn in (pricing.calc_premium, pricing.calc_hedge_cost):
        with pytest.raises(ValueError):
            fn(0, 0.4, 0.1) if fn is pricing.calc_premium else fn(0, 0.4)
    with pytest.raises(ValueError):
        pricing.calc_reserve(-5)


def test_negative_loading_raises():
    with pytest.raises(ValueError):
        pricing.calc_premium(1000, 0.4, -0.1)


def test_solvency_ratio():
    assert pricing.solvency_ratio(1000, 500) == 2.0
    assert pricing.solvency_ratio(1000, 0) == math.inf


def test_can_underwrite_boundary():
    # cash 1000, no existing reserve, new reserve 1000 -> ratio exactly 1.0
    assert pricing.can_underwrite(1000, 0, 1000, 1.0) is True
    # adding a bit more reserve drops below 1.0
    assert pricing.can_underwrite(1000, 0, 1001, 1.0) is False
