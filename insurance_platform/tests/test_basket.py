import pytest
from sqlalchemy import select

from app import ledger, models, risk_engine, settlement
from app.models import Market, Shipment
from app.settlement import InsolvencyError


def _make_shipment(session, user, cargo_value=10000, origin="CityA", destination="CityB"):
    sh = Shipment(
        user_id=user.id, cargo_value=cargo_value, origin=origin, destination=destination
    )
    session.add(sh)
    session.flush()
    return sh


def _assess(sh, cargo_value=10000, loading=0.15):
    return risk_engine.analyze(sh, cargo_value, loading)


def test_underwrite_basket_books_premium_hedge_reserve(session, user):
    sh = _make_shipment(session, user, cargo_value=10000)
    ass = _assess(sh, 10000)
    policy = settlement.underwrite_basket(session, user, sh, ass, 0.15, 1.0)

    assert len(policy.legs) == len(ass)
    assert policy.premium == pytest.approx(sum(a.premium for a in ass))
    assert policy.total_covered == pytest.approx(sum(a.covered_loss for a in ass))

    pool = ledger.recompute_pool(session)
    exp_cash = 10000 + sum(a.premium for a in ass) - sum(a.hedge_cost for a in ass)
    assert pool.cash_balance == pytest.approx(exp_cash)
    assert pool.reserved_liabilities == pytest.approx(sum(a.covered_loss for a in ass))


def test_resolve_factor_yes_pays_leg_and_is_cash_neutral(session, user):
    sh = _make_shipment(session, user, cargo_value=10000)
    ass = _assess(sh, 10000)
    settlement.underwrite_basket(session, user, sh, ass, 0.15, 1.0)
    cash_before = ledger.recompute_pool(session).cash_balance

    # Resolve the port-congestion factor YES.
    market = session.scalar(
        select(Market).where(Market.external_id == "factor-port_congestion")
    )
    result = settlement.resolve_market(session, market, "yes")
    assert result.legs_paid == 1
    leg_covered = 0.08 * 10000
    assert result.total_payout == pytest.approx(leg_covered)

    # YES leg: -covered payout + covered hedge settle => cash unchanged.
    cash_after = ledger.recompute_pool(session).cash_balance
    assert cash_after == pytest.approx(cash_before)


def test_basket_status_transitions(session, user):
    sh = _make_shipment(session, user, cargo_value=10000)
    ass = _assess(sh, 10000)
    policy = settlement.underwrite_basket(session, user, sh, ass, 0.15, 1.0)
    assert policy.status == models.BASKET_ACTIVE

    markets = session.scalars(select(Market).where(Market.provider == "engine")).all()
    for i, m in enumerate(markets):
        settlement.resolve_market(session, m, "no")
        if i < len(markets) - 1:
            assert policy.status == models.BASKET_PARTIALLY_SETTLED
    assert policy.status == models.BASKET_SETTLED


def test_basket_insolvency_blocks(session, user):
    # Pool seeded at 10000; a 400k cargo needs far more reserve than that.
    sh = _make_shipment(session, user, cargo_value=400000)
    ass = _assess(sh, 400000)
    with pytest.raises(InsolvencyError):
        settlement.underwrite_basket(session, user, sh, ass, 0.15, 1.0)
