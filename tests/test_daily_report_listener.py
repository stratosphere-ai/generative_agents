"""End-to-end: bus events flow into Vendy's DailyReportLog via DailyReportListener."""

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
    from vending.daily_report import DailyReportLog
    from vending.supplier import SupplierLedger, matsujime_yokugetsu_matsu

    class _P: pass
    p = _P()
    p.name = "Vendy Unit-001"
    p.vending_state = VendingState(
        inventory={"Coke": 5, "Water": 3},
        prices_cents={"Coke": 150, "Water": 100},
        cash_balance_cents=0,
    )
    p.supplier_ledger = SupplierLedger()
    p.supplier_ledger.add_contract(matsujime_yokugetsu_matsu(
        supplier_id="suntory", label="サントリー",
        sku_unit_costs_cents={"Coke": 60, "Water": 40},
    ))
    p.daily_report_log = DailyReportLog()
    return p


def test_listener_routes_full_day_through_bus(fresh_bus):
    from vending.daily_report_listener import DailyReportListener
    from vending.transaction import sell, OutOfStock

    p = _make_vendy()
    listener = DailyReportListener(p)
    listener.attach()

    # Day boundary: simulator publishes day_start.
    fresh_bus.publish({"kind": "day_start", "persona": "Vendy Unit-001", "date": "2026-04-28"})

    # Two env_ticks set environment + sample traffic.
    fresh_bus.publish({
        "kind":          "env_tick",
        "persona":       "Vendy Unit-001",
        "traffic_index": 0.8,
        "weather_label": "晴",
        "season_label":  "春",
        "holiday_label": "工作日",
    })
    fresh_bus.publish({
        "kind":          "env_tick",
        "persona":       "Vendy Unit-001",
        "traffic_index": 1.6,
        "weather_label": "晴",
        "season_label":  "春",
        "holiday_label": "工作日",
    })

    # Two successful sales (publishes transaction_completed via sell()).
    sell(p.vending_state, sku="Coke", customer="Marco", timestamp_iso="2026-04-28T12:00:00")
    sell(p.vending_state, sku="Coke", customer="Lin",   timestamp_iso="2026-04-28T12:30:00")

    # One out-of-stock: drain Water, then attempt one more.
    sell(p.vending_state, sku="Water", customer="Hana", timestamp_iso="2026-04-28T13:00:00")
    sell(p.vending_state, sku="Water", customer="Hana", timestamp_iso="2026-04-28T13:30:00")
    sell(p.vending_state, sku="Water", customer="Hana", timestamp_iso="2026-04-28T14:00:00")
    with pytest.raises(OutOfStock):
        sell(p.vending_state, sku="Water", customer="Hana", timestamp_iso="2026-04-28T14:30:00")

    # Two action_starts + one sla_blocked propagate through the listener.
    fresh_bus.publish({
        "kind":        "action_start",
        "persona":     "Vendy Unit-001",
        "address":     "x", "duration": 5, "description": "restock",
        "event":       ("Vendy Unit-001", "restocks", "Coke"),
        "action_key":  "k1",
    })
    fresh_bus.publish({
        "kind":        "action_start",
        "persona":     "Vendy Unit-001",
        "address":     "x", "duration": 5, "description": "audit",
        "event":       ("Vendy Unit-001", "audits", "self"),
        "action_key":  "k2",
    })
    fresh_bus.publish({"kind": "sla_blocked", "persona": "Vendy Unit-001"})

    # Day end: report rolls.
    fresh_bus.publish({"kind": "day_end", "persona": "Vendy Unit-001", "date": "2026-04-28"})

    log = p.daily_report_log
    report = log.last_report
    assert report is not None
    # 2 Coke sales (2 × 150) + 3 Water sales (3 × 100) = 600
    assert report.sales_count == 5
    assert report.revenue_cents == 600
    # Cost: 2*60 (Coke) + 3*40 (Water) = 240 (from supplier contract)
    assert report.cost_cents == 240
    assert report.profit_cents == 360
    assert report.missed_count == 1
    assert report.sla_blocked == 1
    assert report.audit_count == 2
    assert report.weather_label == "晴"
    assert report.season_label == "春"
    assert abs(report.avg_traffic - 1.2) < 1e-6
    assert report.peak_traffic == 1.6


def test_listener_ignores_non_vendy_events(fresh_bus):
    from vending.daily_report_listener import DailyReportListener

    p = _make_vendy()
    DailyReportListener(p).attach()

    fresh_bus.publish({"kind": "day_start", "persona": "Marco Silva", "date": "2026-04-28"})
    fresh_bus.publish({
        "kind":      "transaction_completed",
        "persona":   "Marco Silva",
        "sku":       "Coke", "price_cents": 150, "customer": "Lin",
        "timestamp": "2026-04-28T12:00:00",
        "parent_local_uuid": None,
    })
    # No begin_day fired because day_start was for Marco; close_day should
    # silently skip (no-op) instead of raising.
    fresh_bus.publish({"kind": "day_end", "persona": "Vendy Unit-001", "date": "2026-04-28"})
    assert p.daily_report_log.last_report is None
    assert p.daily_report_log.cumulative.sales_count == 0


def test_close_day_uses_supplier_next_due_when_present(fresh_bus):
    from vending.daily_report_listener import DailyReportListener

    p = _make_vendy()
    p.supplier_ledger.place_order("suntory", [("Coke", 24)], _dt.date(2026, 4, 5))
    p.supplier_ledger.tick(_dt.date(2026, 4, 30), cash_balance_cents=10_000)

    DailyReportListener(p).attach()
    fresh_bus.publish({"kind": "day_start", "persona": "Vendy Unit-001", "date": "2026-04-30"})
    fresh_bus.publish({"kind": "day_end",   "persona": "Vendy Unit-001", "date": "2026-04-30"})

    assert p.daily_report_log.last_report.next_due_date_iso == "2026-05-31"


def test_try_sell_returns_none_on_stockout_and_publishes_missed(fresh_bus):
    from vending.transaction import try_sell
    from vending.state import VendingState

    seen: list = []
    fresh_bus.subscribe(lambda e: seen.append(e))

    state = VendingState(inventory={"Coke": 0}, prices_cents={"Coke": 150})
    result = try_sell(state, sku="Coke", customer="x", timestamp_iso="t")
    assert result is None
    assert any(e["kind"] == "transaction_missed" and e["reason"] == "out_of_stock" for e in seen)
