"""Tests for vending/auto_loan.py — SLA-bounded auto-loans on supplier shortfall."""

from __future__ import annotations

import datetime as _dt
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "reverie" / "backend_server"))


@pytest.fixture()
def fresh_bus():
    from vending.events import bus
    bus.reset()
    yield bus
    bus.reset()


def _make_vendy(*, cash_cents: int = 0, outstanding_cents: int = 0):
    from vending.state import VendingState
    from vending.sla import SLAState
    from vending.price_governance import PriceGovernance

    class _P: pass
    p = _P()
    p.name = "Vendy Unit-001"
    p.vending_state = VendingState(
        inventory={"Coke": 12},
        prices_cents={"Coke": 150},
        cash_balance_cents=cash_cents,
        outstanding_loans_cents=outstanding_cents,
    )
    p.sla_state        = SLAState()
    p.price_governance = PriceGovernance()
    return p


def test_loan_granted_within_sla_cap(fresh_bus):
    from vending.auto_loan import request_loan_for_shortfall

    seen: list = []
    fresh_bus.subscribe(lambda e: seen.append(e))

    p = _make_vendy()
    res = request_loan_for_shortfall(p, shortfall_cents=3000,    # ¥30
                                     now=_dt.datetime(2026, 4, 28, 0, 0, 0),
                                     reason="settle invoice INV-1")
    # 3000 * 1.10 = 3300 → round up to 10000 (¥100 increment)
    assert res.granted_cents == 10_000
    assert res.assessment.allowed is True
    assert "committed" in res.summary
    assert p.vending_state.cash_balance_cents == 10_000
    assert p.vending_state.outstanding_loans_cents == 10_000
    # business_action_committed event published with LOAN_REQUEST action.
    assert any(e["kind"] == "business_action_committed" and e["action"] == "LOAN_REQUEST"
               for e in seen)


def test_loan_blocked_when_projected_total_exceeds_cap(fresh_bus):
    from vending.auto_loan import request_loan_for_shortfall

    seen: list = []
    fresh_bus.subscribe(lambda e: seen.append(e))

    # Already deeply in debt; SLA max_loan_cents is ¥140 = 14000.
    p = _make_vendy(outstanding_cents=12_000)
    res = request_loan_for_shortfall(p, shortfall_cents=6_000,    # +¥60 → projected 18000+ > cap
                                     now=_dt.datetime(2026, 4, 28, 0, 0, 0))
    assert res.granted_cents == 0
    assert res.assessment.allowed is False
    assert "BLOCKED" in res.summary
    # State untouched, sla_blocked event present, no commit event.
    assert p.vending_state.cash_balance_cents      == 0
    assert p.vending_state.outstanding_loans_cents == 12_000
    assert any(e["kind"] == "sla_blocked" for e in seen)
    assert not any(e["kind"] == "business_action_committed" for e in seen)


def test_loan_rounds_up_to_100_yen_buckets(fresh_bus):
    from vending.auto_loan import request_loan_for_shortfall
    p = _make_vendy()
    # Shortfall 50¥ + 10% buffer = 55¥ → rounds up to ¥100 = 10000 cents.
    res = request_loan_for_shortfall(p, shortfall_cents=5_000,
                                     now=_dt.datetime(2026, 4, 28, 0, 0, 0))
    assert res.granted_cents == 10_000


def test_loan_zero_shortfall_short_circuits(fresh_bus):
    from vending.auto_loan import request_loan_for_shortfall
    p = _make_vendy()
    res = request_loan_for_shortfall(p, shortfall_cents=0,
                                     now=_dt.datetime(2026, 4, 28, 0, 0, 0))
    assert res.granted_cents == 0
    assert res.assessment is None
    assert "no shortfall" in res.summary


def test_supplier_insufficient_funds_triggers_auto_loan_end_to_end(fresh_bus):
    """Simulate the reverie midnight retry flow: tick raises, loan covers, retry succeeds."""
    from vending.auto_loan import request_loan_for_shortfall
    from vending.supplier import SupplierLedger, matsujime_yokugetsu_matsu, InsufficientFundsError

    p = _make_vendy(cash_cents=0)
    p.supplier_ledger = SupplierLedger()
    p.supplier_ledger.add_contract(matsujime_yokugetsu_matsu(
        supplier_id="suntory", label="サントリー",
        sku_unit_costs_cents={"Coke": 60},
    ))
    p.supplier_ledger.place_order("suntory", [("Coke", 12)], _dt.date(2026, 4, 5))
    p.supplier_ledger.tick(_dt.date(2026, 4, 30), cash_balance_cents=10_000_000)  # cut

    # Simulate empty cash on payment day.
    p.vending_state.cash_balance_cents = 0
    try:
        p.supplier_ledger.tick(_dt.date(2026, 5, 31), cash_balance_cents=p.vending_state.cash_balance_cents)
        pytest.fail("expected InsufficientFundsError")
    except InsufficientFundsError as exc:
        loan = request_loan_for_shortfall(
            p, exc.shortfall_cents, now=_dt.datetime(2026, 5, 31, 0, 0, 0),
            reason=f"settle invoice {exc.invoice_id}",
        )
        assert loan.granted_cents > 0

    # Retry the supplier tick — cash now sufficient.
    res = p.supplier_ledger.tick(_dt.date(2026, 5, 31),
                                 cash_balance_cents=p.vending_state.cash_balance_cents)
    assert len(res["paid"]) == 1
    assert p.supplier_ledger.payable_total_cents() == 0
