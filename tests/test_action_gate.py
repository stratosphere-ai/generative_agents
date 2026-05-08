"""Tests for vending/action_gate.py — SLA + governance gate around business actions."""

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


def _make_vendy():
    from vending.state import VendingState
    from vending.sla import SLAState
    from vending.price_governance import PriceGovernance
    from vending.supplier import SupplierLedger, matsujime_yokugetsu_matsu

    class _P: pass
    p = _P()
    p.name = "Vendy Unit-001"
    p.vending_state = VendingState(
        inventory={"Coke": 12, "Water": 8},
        prices_cents={"Coke": 150, "Water": 100},
    )
    p.sla_state         = SLAState()
    p.price_governance  = PriceGovernance()
    p.supplier_ledger   = SupplierLedger()
    p.supplier_ledger.add_contract(matsujime_yokugetsu_matsu(
        supplier_id="suntory", label="サントリー",
        sku_unit_costs_cents={"Coke": 60, "Water": 40},
    ))
    return p


# ---- gate_action ----------------------------------------------------------


def test_gate_blocks_oversized_loan_and_emits_sla_blocked(fresh_bus):
    from vending.action_gate import gate_action

    p = _make_vendy()
    seen = []
    fresh_bus.subscribe(lambda e: seen.append(e))

    res = gate_action(p, "LOAN_REQUEST", payload={"projected_loan_cents": 30_000})
    assert res.allowed is False
    assert p.sla_state.blocked == 1
    assert any(e["kind"] == "sla_blocked" and e["action"] == "LOAN_REQUEST" for e in seen)


def test_gate_allows_modest_action_no_sla_block(fresh_bus):
    from vending.action_gate import gate_action
    p = _make_vendy()
    res = gate_action(p, "RESTOCK_ORDER",
                      payload={"order_cost_cents": 1500},
                      observation={"stock_at_risk": ["Water"]})
    assert res.allowed is True
    assert p.sla_state.blocked == 0


def test_gate_passthrough_when_no_sla(fresh_bus):
    from vending.action_gate import gate_action

    class _P: pass
    p = _P()
    res = gate_action(p, "LOAN_REQUEST", payload={"projected_loan_cents": 99_999})
    assert res.allowed is True
    assert res.risk == 0


# ---- commit_price_change --------------------------------------------------


def test_commit_price_change_applies_when_allowed(fresh_bus):
    from vending.action_gate import commit_price_change
    p = _make_vendy()
    now = _dt.datetime(2026, 4, 28, 12, 0, 0)

    res = commit_price_change(p, sku="Coke", new_price_cents=160, now=now)
    assert res.allowed is True
    assert p.vending_state.prices_cents["Coke"] == 160
    # Governance recorded the change so a follow-up immediate change is blocked.
    follow = commit_price_change(p, sku="Coke", new_price_cents=170,
                                 now=now + _dt.timedelta(hours=1))
    assert follow.allowed is False
    assert p.vending_state.prices_cents["Coke"] == 160  # unchanged
    assert p.sla_state.blocked >= 1


def test_commit_price_change_blocks_oversized_step(fresh_bus):
    from vending.action_gate import commit_price_change
    p = _make_vendy()
    now = _dt.datetime(2026, 4, 28, 12, 0, 0)

    # Coke 150 -> 250 = +66% delta, blows past max_price_change=45%
    res = commit_price_change(p, sku="Coke", new_price_cents=250, now=now)
    assert res.allowed is False
    assert "保单限制" in "，".join(res.reasons)
    assert p.vending_state.prices_cents["Coke"] == 150


def test_commit_price_change_within_governance_step_succeeds(fresh_bus):
    from vending.action_gate import commit_price_change
    p = _make_vendy()
    now = _dt.datetime(2026, 4, 28, 12, 0, 0)
    # +6.6% (158 from 150) — within governance 12% step + SLA 45%
    res = commit_price_change(p, sku="Coke", new_price_cents=158, now=now)
    assert res.allowed is True


# ---- commit_restock -------------------------------------------------------


def test_commit_restock_places_order_when_allowed(fresh_bus):
    from vending.action_gate import commit_restock
    p = _make_vendy()
    res = commit_restock(p, supplier_id="suntory",
                         items=[("Coke", 12), ("Water", 12)],
                         order_date=_dt.date(2026, 4, 5),
                         observation={"stock_at_risk": ["Water"]})
    assert res.allowed is True
    assert len(p.supplier_ledger.open_orders) == 1
    assert p.supplier_ledger.open_orders[0].items == [("Coke", 12), ("Water", 12)]


def test_commit_restock_blocks_oversized_order(fresh_bus):
    from vending.action_gate import commit_restock
    p = _make_vendy()
    # 200 Coke at 60 cents = 12000 cents = ¥120, well above ¥48 cap
    res = commit_restock(p, supplier_id="suntory",
                         items=[("Coke", 200)],
                         order_date=_dt.date(2026, 4, 5))
    assert res.allowed is False
    assert "订单金额过高" in res.reasons
    assert len(p.supplier_ledger.open_orders) == 0


def test_commit_restock_unknown_supplier_raises():
    from vending.action_gate import commit_restock
    p = _make_vendy()
    with pytest.raises(RuntimeError):
        commit_restock(p, supplier_id="unknown",
                       items=[("Coke", 1)], order_date=_dt.date(2026, 4, 5))


# ---- end-to-end with DailyReportListener ---------------------------------


def test_blocked_action_increments_daily_report_via_bus(fresh_bus):
    from vending.action_gate import gate_action
    from vending.daily_report import DailyReportLog
    from vending.daily_report_listener import DailyReportListener

    p = _make_vendy()
    p.daily_report_log = DailyReportLog()
    DailyReportListener(p).attach()

    fresh_bus.publish({"kind": "day_start", "persona": p.name, "date": "2026-04-28"})
    gate_action(p, "LOAN_REQUEST", payload={"projected_loan_cents": 30_000})
    fresh_bus.publish({"kind": "day_end",   "persona": p.name, "date": "2026-04-28"})

    assert p.daily_report_log.last_report.sla_blocked == 1
