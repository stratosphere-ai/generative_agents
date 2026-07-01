import pytest

from app import ledger, models, settlement
from app.settlement import InsolvencyError


def _pool(session):
    return ledger.recompute_pool(session)


def test_underwrite_records_premium_hedge_reserve(session, user, market):
    policy = settlement.underwrite_policy(session, user, market, 1000, 0.15, 1.0)
    assert policy.status == models.POLICY_ACTIVE
    assert policy.premium == pytest.approx(460.0)  # 1000*0.4*1.15
    assert policy.reserve == 1000

    pool = _pool(session)
    # cash = 10000 seed + 460 premium - 400 hedge = 10060
    assert pool.cash_balance == pytest.approx(10060.0)
    assert pool.reserved_liabilities == pytest.approx(1000.0)
    assert pool.hedge_asset_value == pytest.approx(400.0)


def test_resolve_yes_pays_claim(session, user, market):
    settlement.underwrite_policy(session, user, market, 1000, 0.15, 1.0)
    result = settlement.resolve_market(session, market, "yes")

    assert result.policies_paid == 1
    assert result.total_payout == pytest.approx(1000.0)
    assert market.status == models.MARKET_RESOLVED_YES

    pool = _pool(session)
    # cash = 10060 - 1000 payout + 1000 hedge settle = 10060
    assert pool.cash_balance == pytest.approx(10060.0)
    assert pool.reserved_liabilities == pytest.approx(0.0)
    assert pool.hedge_asset_value == pytest.approx(0.0)
    # Net gain over seed capital = the loading margin = 1000*0.4*0.15 = 60
    assert pool.cash_balance - 10000 == pytest.approx(60.0)


def test_resolve_no_expires_worthless(session, user, market):
    settlement.underwrite_policy(session, user, market, 1000, 0.15, 1.0)
    result = settlement.resolve_market(session, market, "no")

    assert result.policies_expired == 1
    assert result.total_payout == 0.0
    assert market.status == models.MARKET_RESOLVED_NO

    pool = _pool(session)
    # cash = 10060 (no payout); net gain = premium - hedge loss = 460 - 400 = 60
    assert pool.cash_balance == pytest.approx(10060.0)
    assert pool.cash_balance - 10000 == pytest.approx(60.0)
    assert pool.reserved_liabilities == pytest.approx(0.0)


def test_insolvency_blocks_underwriting(session, user, market):
    # Seed pool is 10000; a 20000 coverage needs 20000 reserve -> ratio < 1.
    with pytest.raises(InsolvencyError):
        settlement.underwrite_policy(session, user, market, 20000, 0.15, 1.0)


def test_cannot_resolve_twice(session, user, market):
    settlement.underwrite_policy(session, user, market, 1000, 0.15, 1.0)
    settlement.resolve_market(session, market, "yes")
    with pytest.raises(ValueError):
        settlement.resolve_market(session, market, "no")
