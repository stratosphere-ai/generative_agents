"""Tests for vending/supplier.py (Japanese commercial cycle) + vending/daily_report.py."""

from __future__ import annotations

import datetime as _dt
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "reverie" / "backend_server"))


# ---------------------------------------------------------------------------
# Supplier — Japanese settlement cycles
# ---------------------------------------------------------------------------


def test_matsujime_yokugetsu_matsu_invoice_runs_at_month_end_and_due_next_month_end():
    from vending.supplier import (SupplierLedger, matsujime_yokugetsu_matsu)
    ledger = SupplierLedger()
    ledger.add_contract(matsujime_yokugetsu_matsu(
        supplier_id="suntory", label="サントリー",
        sku_unit_costs_cents={"Coke": 60, "Water": 40},
    ))

    # Place 3 orders during April.
    ledger.place_order("suntory", [("Coke", 24)], _dt.date(2026, 4, 5))
    ledger.place_order("suntory", [("Water", 24)], _dt.date(2026, 4, 18))
    ledger.place_order("suntory", [("Coke", 12), ("Water", 12)], _dt.date(2026, 4, 28))

    # Day before cutoff: nothing happens.
    res = ledger.tick(_dt.date(2026, 4, 29), cash_balance_cents=100_000)
    assert res["cut"] == [] and res["paid"] == []
    assert ledger.payable_total_cents() == 0

    # 4/30 = 末締め. Three orders aggregate into one invoice.
    res = ledger.tick(_dt.date(2026, 4, 30), cash_balance_cents=100_000)
    assert len(res["cut"]) == 1
    inv = res["cut"][0]
    # Subtotal: 24*60 (1440) + 24*40 (960) + 12*60 (720) + 12*40 (480) = 3600
    assert inv.subtotal_cents == 3600
    # 8% reduced consumption tax = 288, +220 transfer fee
    assert inv.tax_cents == 288
    assert inv.transfer_fee_cents == 220
    assert inv.total_cents == 3600 + 288 + 220
    # 翌月末 = 5/31
    assert inv.due_date == _dt.date(2026, 5, 31)

    # Payable carries forward through May.
    assert ledger.payable_total_cents() == inv.total_cents
    res = ledger.tick(_dt.date(2026, 5, 30), cash_balance_cents=100_000)
    assert res["paid"] == []

    # 5/31 = 支払日: cash debited.
    res = ledger.tick(_dt.date(2026, 5, 31), cash_balance_cents=100_000)
    assert len(res["paid"]) == 1
    assert ledger.payable_total_cents() == 0
    assert res["cash_after_cents"] == 100_000 - inv.total_cents


def test_hatsuka_jime_yokugetsu_tooka_uses_short_cycle():
    from vending.supplier import SupplierLedger, hatsuka_jime_yokugetsu_tooka
    ledger = SupplierLedger()
    ledger.add_contract(hatsuka_jime_yokugetsu_tooka(
        supplier_id="calbee", label="カルビー",
        sku_unit_costs_cents={"Chips": 80},
    ))
    ledger.place_order("calbee", [("Chips", 12)], _dt.date(2026, 4, 5))

    # Cutoff 4/20.
    res = ledger.tick(_dt.date(2026, 4, 20), cash_balance_cents=10_000)
    assert len(res["cut"]) == 1
    inv = res["cut"][0]
    assert inv.due_date == _dt.date(2026, 5, 10)
    expected_total = 12 * 80 + round(12 * 80 * 0.08) + 220
    assert inv.total_cents == expected_total

    # 5/10 settlement.
    res = ledger.tick(_dt.date(2026, 5, 10), cash_balance_cents=10_000)
    assert len(res["paid"]) == 1


def test_sokukin_no_transfer_fee_same_day_settlement():
    from vending.supplier import SupplierLedger, sokukin
    ledger = SupplierLedger()
    ledger.add_contract(sokukin(
        supplier_id="local", label="町の商店",
        sku_unit_costs_cents={"Chocolate": 70},
    ))
    ledger.place_order("local", [("Chocolate", 6)], _dt.date(2026, 4, 12))

    # End of April: cutoff and pay same day.
    res = ledger.tick(_dt.date(2026, 4, 30), cash_balance_cents=10_000)
    assert len(res["cut"]) == 1
    inv = res["cut"][0]
    assert inv.transfer_fee_cents == 0
    assert inv.due_date == _dt.date(2026, 4, 30)
    assert len(res["paid"]) == 1


def test_february_cutoff_31_clamps_to_28():
    from vending.supplier import SupplierLedger, matsujime_yokugetsu_matsu
    ledger = SupplierLedger()
    ledger.add_contract(matsujime_yokugetsu_matsu(
        supplier_id="x", label="X商事",
        sku_unit_costs_cents={"Coke": 60},
    ))
    ledger.place_order("x", [("Coke", 1)], _dt.date(2025, 2, 5))
    res = ledger.tick(_dt.date(2025, 2, 28), cash_balance_cents=10_000)
    assert len(res["cut"]) == 1
    assert res["cut"][0].cutoff_date == _dt.date(2025, 2, 28)
    # Due 3/31.
    assert res["cut"][0].due_date == _dt.date(2025, 3, 31)


def test_february_2024_leap_year_uses_29():
    from vending.supplier import SupplierLedger, matsujime_yokugetsu_matsu
    ledger = SupplierLedger()
    ledger.add_contract(matsujime_yokugetsu_matsu(
        supplier_id="x", label="X商事",
        sku_unit_costs_cents={"Coke": 60},
    ))
    ledger.place_order("x", [("Coke", 1)], _dt.date(2024, 2, 5))
    res = ledger.tick(_dt.date(2024, 2, 29), cash_balance_cents=10_000)
    assert len(res["cut"]) == 1
    assert res["cut"][0].cutoff_date == _dt.date(2024, 2, 29)


def test_insufficient_funds_on_payment_day_raises():
    from vending.supplier import SupplierLedger, matsujime_yokugetsu_matsu, InsufficientFundsError
    ledger = SupplierLedger()
    ledger.add_contract(matsujime_yokugetsu_matsu(
        supplier_id="suntory", label="サントリー",
        sku_unit_costs_cents={"Coke": 60},
    ))
    ledger.place_order("suntory", [("Coke", 100)], _dt.date(2026, 4, 5))   # 6000 + tax + fee

    ledger.tick(_dt.date(2026, 4, 30), cash_balance_cents=999_999)
    # Drained cash before payment day.
    with pytest.raises(InsufficientFundsError) as excinfo:
        ledger.tick(_dt.date(2026, 5, 31), cash_balance_cents=100)
    assert excinfo.value.shortfall_cents > 0


def test_ledger_round_trip_json(tmp_path):
    from vending.supplier import SupplierLedger, matsujime_yokugetsu_matsu
    p = tmp_path / "ledger.json"
    ledger = SupplierLedger()
    ledger.add_contract(matsujime_yokugetsu_matsu(
        supplier_id="suntory", label="サントリー",
        sku_unit_costs_cents={"Coke": 60},
    ))
    ledger.place_order("suntory", [("Coke", 12)], _dt.date(2026, 4, 5))
    ledger.tick(_dt.date(2026, 4, 30), cash_balance_cents=100_000)
    ledger.save(p)

    reloaded = SupplierLedger.load(p)
    assert "suntory" in reloaded.contracts
    assert reloaded.payable_total_cents() == ledger.payable_total_cents()
    assert reloaded.invoices[0].due_date == _dt.date(2026, 5, 31)


def test_summary_lines_lists_payable_and_next_due():
    from vending.supplier import SupplierLedger, matsujime_yokugetsu_matsu
    ledger = SupplierLedger()
    ledger.add_contract(matsujime_yokugetsu_matsu(
        supplier_id="suntory", label="サントリー",
        sku_unit_costs_cents={"Coke": 60},
    ))
    ledger.place_order("suntory", [("Coke", 24)], _dt.date(2026, 4, 5))
    ledger.tick(_dt.date(2026, 4, 30), cash_balance_cents=100_000)

    summary = "\n".join(ledger.summary_lines())
    assert "サントリー" in summary
    assert "末締め" in summary or "末締" in summary
    assert "買掛金" in summary
    assert "次回支払" in summary


# ---------------------------------------------------------------------------
# DailyReportLog
# ---------------------------------------------------------------------------


def test_daily_report_aggregates_sales_and_misses():
    from vending.daily_report import DailyReportLog
    log = DailyReportLog()
    log.begin_day("2026-04-28", cash_balance_cents=10_000, payable_cents=0)
    log.record_environment(weather_label="晴", season_label="春", holiday_label="工作日")
    log.record_traffic_sample(0.8)
    log.record_traffic_sample(1.6)
    log.record_sale(price_cents=150, cost_cents=60)
    log.record_sale(price_cents=150, cost_cents=60)
    log.record_missed()
    log.record_block()
    log.record_audit()

    report = log.close_day(cash_balance_cents=10_180, payable_cents=500)

    assert report.sales_count == 2
    assert report.revenue_cents == 300
    assert report.cost_cents == 120
    assert report.profit_cents == 180
    assert report.missed_count == 1
    assert report.sla_blocked == 1
    assert report.audit_count == 1
    assert abs(report.avg_traffic - 1.2) < 1e-6
    assert report.peak_traffic == 1.6
    assert report.payable_delta_cents == 500
    assert "销售 2 单" in report.thought_text
    assert log.last_report is report


def test_daily_report_close_without_begin_raises():
    from vending.daily_report import DailyReportLog
    log = DailyReportLog()
    with pytest.raises(RuntimeError):
        log.close_day(cash_balance_cents=0, payable_cents=0)


def test_daily_report_summary_lines_before_first_close():
    from vending.daily_report import DailyReportLog
    log = DailyReportLog()
    out = "\n".join(log.summary_lines())
    assert "首日" in out


def test_daily_report_history_caps_at_history_cap():
    from vending.daily_report import DailyReportLog
    log = DailyReportLog(history_cap=3)
    for d in range(1, 8):
        log.begin_day(f"2026-04-0{d}", cash_balance_cents=0, payable_cents=0)
        log.record_environment(weather_label="—", season_label="—", holiday_label="—")
        log.close_day(cash_balance_cents=0, payable_cents=0)
    assert len(log.history) == 3
    assert log.history[0].date_iso == "2026-04-07"


def test_daily_report_round_trip_json(tmp_path):
    from vending.daily_report import DailyReportLog
    p = tmp_path / "report.json"
    log = DailyReportLog()
    log.begin_day("2026-04-28", cash_balance_cents=0, payable_cents=0)
    log.record_environment(weather_label="雨", season_label="春", holiday_label="工作日")
    log.record_sale(price_cents=100, cost_cents=40)
    log.close_day(cash_balance_cents=60, payable_cents=0)
    log.save(p)

    reloaded = DailyReportLog.load(p)
    assert reloaded.last_report is not None
    assert reloaded.last_report.weather_label == "雨"
    assert reloaded.last_report.profit_cents == 60


# ---------------------------------------------------------------------------
# Prompt preamble integration
# ---------------------------------------------------------------------------


def test_state_preamble_includes_supplier_and_report_sections():
    from vending.prompt_inject import state_preamble
    from vending.state import VendingState
    from vending.supplier import SupplierLedger, matsujime_yokugetsu_matsu
    from vending.daily_report import DailyReportLog

    class _P: pass
    p = _P()
    p.vending_state    = VendingState(inventory={"Coke": 5}, prices_cents={"Coke": 150})

    ledger = SupplierLedger()
    ledger.add_contract(matsujime_yokugetsu_matsu(
        supplier_id="suntory", label="サントリー",
        sku_unit_costs_cents={"Coke": 60},
    ))
    p.supplier_ledger = ledger

    rep = DailyReportLog()
    rep.begin_day("2026-04-28", cash_balance_cents=0, payable_cents=0)
    rep.record_environment(weather_label="晴", season_label="春", holiday_label="工作日")
    rep.record_sale(price_cents=150, cost_cents=60)
    rep.close_day(cash_balance_cents=90, payable_cents=0)
    p.daily_report_log = rep

    out = state_preamble(p)
    assert "## Supplier ledger" in out
    assert "サントリー" in out
    assert "## Yesterday's daily report" in out
    assert "销售 1 单" in out
