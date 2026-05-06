"""Tests for SLA guardrails + price governance + prompt preamble integration."""

from __future__ import annotations

import datetime as _dt
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "reverie" / "backend_server"))


# --- SLA -------------------------------------------------------------------


def test_sla_blocks_oversized_loan():
    from vending.sla import SLAState
    sla = SLAState()
    res = sla.assess("LOAN_REQUEST", payload={"projected_loan_cents": 20_000})
    assert res.allowed is False
    assert any("贷款" in r for r in res.reasons)
    assert res.risk > sla.risk_limit


def test_sla_allows_small_restock_when_stock_at_risk():
    from vending.sla import SLAState
    sla = SLAState()
    res = sla.assess(
        "RESTOCK_ORDER",
        payload={"order_cost_cents": 2_000},
        observation={"stock_at_risk": ["Coke", "Water"]},
    )
    assert res.allowed is True


def test_sla_blocks_oversized_price_change():
    from vending.sla import SLAState
    sla = SLAState()
    res = sla.assess("PRICE_CHANGE", payload={
        "previous_price_cents": 100,
        "next_price_cents":     200,    # +100% delta
    })
    assert res.allowed is False
    assert "调价幅度超过保单限制" in res.reasons


def test_sla_round_trip(tmp_path):
    from vending.sla import SLAState
    p = tmp_path / "sla.json"
    s = SLAState(blocked=3, claims=1, premium_paid_cents=500)
    s.save(p)
    loaded = SLAState.load(p)
    assert loaded.blocked == 3
    assert loaded.premium_paid_cents == 500


def test_sla_disabled_always_allows():
    from vending.sla import SLAState
    sla = SLAState(enabled=False)
    res = sla.assess("LOAN_REQUEST", payload={"projected_loan_cents": 99_999})
    assert res.allowed is True


# --- Price governance ------------------------------------------------------


def test_governance_rejects_when_within_cooldown():
    from vending.price_governance import PriceGovernance
    g = PriceGovernance()
    now = _dt.datetime(2026, 4, 28, 12, 0, 0)
    g.record_change("Coke", 100, 110, now)
    later = now + _dt.timedelta(hours=2)
    ok, reasons = g.can_change_price("Coke", 110, 120, later)
    assert ok is False
    assert any("观察窗口" in r for r in reasons)


def test_governance_rejects_when_daily_cap_used():
    from vending.price_governance import PriceGovernance
    g = PriceGovernance(min_interval_hours=0)
    day = _dt.datetime(2026, 4, 28, 8, 0, 0)
    g.record_change("Water", 100, 105, day)
    ok, reasons = g.can_change_price("Water", 105, 108, day + _dt.timedelta(minutes=1))
    assert ok is False
    assert any("调价次数" in r for r in reasons)


def test_governance_rejects_when_step_too_large():
    from vending.price_governance import PriceGovernance
    g = PriceGovernance()
    now = _dt.datetime(2026, 4, 28, 12, 0, 0)
    ok, reasons = g.can_change_price("Coke", 100, 130, now)   # 30% jump
    assert ok is False
    assert any("调价幅度" in r for r in reasons)


def test_governance_resets_daily_counter_on_new_day():
    from vending.price_governance import PriceGovernance
    g = PriceGovernance(min_interval_hours=0)
    d1 = _dt.datetime(2026, 4, 28, 23, 0, 0)
    g.record_change("Coffee", 100, 105, d1)
    ok, _ = g.can_change_price("Coffee", 105, 108, d1 + _dt.timedelta(minutes=10))
    assert ok is False
    d2 = _dt.datetime(2026, 4, 29, 9, 0, 0)
    ok, _ = g.can_change_price("Coffee", 105, 108, d2)
    assert ok is True


# --- Prompt preamble -------------------------------------------------------


def test_state_preamble_includes_sla_and_governance():
    from vending.prompt_inject import state_preamble
    from vending.state import VendingState
    from vending.sla import SLAState
    from vending.price_governance import PriceGovernance

    class _P: pass
    p = _P()
    p.vending_state    = VendingState(inventory={"Coke": 5}, prices_cents={"Coke": 150})
    p.sla_state        = SLAState()
    p.price_governance = PriceGovernance()

    out = state_preamble(p)
    assert "## SLA guardrails" in out
    assert "## Price governance" in out
    assert "风险上限" in out
    assert "调价幅度上限" in out


def test_state_preamble_omits_sections_when_no_sla():
    from vending.prompt_inject import state_preamble
    from vending.state import VendingState

    class _P: pass
    p = _P()
    p.vending_state = VendingState(inventory={"Coke": 5}, prices_cents={"Coke": 150})

    out = state_preamble(p)
    assert "SLA" not in out
    assert "Price governance" not in out
