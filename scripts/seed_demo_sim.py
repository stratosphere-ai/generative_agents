"""Build a self-contained demo sim under storage/<sim_code>/.

Hand-crafted JSON files seed the six /api/vending/* endpoints with
believable mid-day data so the dashboard's Live mode renders something
interesting without needing reverie + anvil + OpenAI all running.

Usage:
    python scripts/seed_demo_sim.py [--sim demo_plaza_run]
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
STORAGE   = REPO_ROOT / "environment" / "frontend_server" / "storage"


def _write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False))


def build(sim_code: str) -> None:
    sim_dir = STORAGE / sim_code
    if sim_dir.exists():
        # Remove old fixture before re-seeding so dashboard sees fresh data.
        import shutil
        shutil.rmtree(sim_dir)
    bm = sim_dir / "personas" / "Vendy Unit-001" / "bootstrap_memory"

    _write(sim_dir / "reverie" / "meta.json", {
        "fork_sim_code": "base_vending_min",
        "start_date":    "April 28, 2026",
        "curr_time":     "April 28, 2026, 12:42:00",
        "sec_per_step":  10,
        "maze_name":     "vending",
        "persona_names": ["Vendy Unit-001", "Lin Wei", "Marco Silva", "Hana Park"],
        "step":           756,
    })

    _write(sim_dir / "environment" / "756.json", {
        "Vendy Unit-001": {"maze": "vending", "x": 5,  "y": 10},
        "Lin Wei":        {"maze": "vending", "x": 22, "y": 12},
        "Marco Silva":    {"maze": "vending", "x": 6,  "y": 11},   # 在 Vendy 旁边买东西
        "Hana Park":      {"maze": "vending", "x": 30, "y": 24},
    })

    _write(bm / "scratch.json", {"name": "Vendy Unit-001",
                                  "daily_plan_req": "operate; restock pre-peak; log on chain"})
    _write(bm / "vending_state.json", {
        "inventory": {"Coke": 14, "Water": 9, "Coffee": 7, "Chips": 6, "Chocolate": 3},
        "prices_cents": {"Coke": 158, "Water": 105, "Coffee": 250, "Chips": 200, "Chocolate": 175},
        "cash_balance_cents": 2840,
        "power_pct": 87.5,
        "last_restock_iso": "2026-04-28T08:15:00",
        "wallet_address": "0xa11ce0000000000000000000000000000000abcd",
        "outstanding_loans_cents": 10000,
    })

    _write(bm / "sla_state.json", {
        "enabled": True,
        "coverage_pool_cents": 12500,
        "premium_paid_cents": 850,
        "blocked": 2,
        "claims": 0,
        "max_loan_cents": 14000,
        "max_order_cost_cents": 4800,
        "max_price_change": 0.45,
        "risk_limit": 72,
        "last_assessment": "PRICE_CHANGE 风险 38",
    })

    _write(bm / "price_governance.json", {
        "min_interval_hours": 12,
        "max_daily_changes": 1,
        "max_single_change_rate": 0.12,
        "last_changed":  {"Coke": "2026-04-28T08:30:00"},
        "changes_today": {"Coke": 1},
        "today_iso_date": "2026-04-28",
        "last_action": "Coke ¥1.50 → ¥1.58 (+5.3%)",
    })

    _write(bm / "supplier_ledger.json", {
        "_seq": 8,
        "contracts": {
            "suntory": {
                "supplier_id": "suntory", "label": "サントリー",
                "cutoff_day": 31, "payment_offset_months": 1, "payment_day": 31,
                "consumption_tax_rate": 0.08, "transfer_fee_cents_buyer": 220,
                "early_payment_discount": 0.0,
                "sku_unit_costs_cents": {"Coke": 60, "Water": 40, "Coffee": 100},
            },
            "calbee": {
                "supplier_id": "calbee", "label": "カルビー",
                "cutoff_day": 20, "payment_offset_months": 1, "payment_day": 10,
                "consumption_tax_rate": 0.08, "transfer_fee_cents_buyer": 220,
                "early_payment_discount": 0.0,
                "sku_unit_costs_cents": {"Chips": 80, "Chocolate": 70},
            },
        },
        "open_orders": [
            {"order_id": "PO-00007", "supplier_id": "suntory", "order_date": "2026-04-28",
             "items": [["Coke", 24], ["Water", 12]], "invoiced": False},
        ],
        "invoices": [
            {"invoice_id": "INV-00005", "supplier_id": "suntory",
             "cutoff_date": "2026-04-30", "due_date": "2026-05-31",
             "lines": [{"sku": "Coke", "quantity": 36, "unit_cost_cents": 60},
                       {"sku": "Water", "quantity": 24, "unit_cost_cents": 40}],
             "subtotal_cents": 3120, "tax_cents": 250, "transfer_fee_cents": 220,
             "total_cents": 3590, "paid": False, "paid_date": None},
            {"invoice_id": "INV-00006", "supplier_id": "calbee",
             "cutoff_date": "2026-04-20", "due_date": "2026-05-10",
             "lines": [{"sku": "Chips", "quantity": 24, "unit_cost_cents": 80}],
             "subtotal_cents": 1920, "tax_cents": 154, "transfer_fee_cents": 220,
             "total_cents": 2294, "paid": False, "paid_date": None},
        ],
        "history": [
            {"invoice_id": "INV-00003", "supplier_id": "suntory",
             "cutoff_date": "2026-03-31", "due_date": "2026-04-30",
             "lines": [{"sku": "Coke", "quantity": 24, "unit_cost_cents": 60}],
             "subtotal_cents": 1440, "tax_cents": 115, "transfer_fee_cents": 220,
             "total_cents": 1775, "paid": True, "paid_date": "2026-04-30"},
        ],
    })

    _write(bm / "daily_report.json", {
        "today_date_iso": "2026-04-28",
        "today_start": {"date_iso": "2026-04-28", "revenue_cents": 1850, "cost_cents": 720,
                        "sales_count": 12, "missed_count": 1,
                        "cash_balance_cents": 1420, "payable_cents": 5550,
                        "audit_count": 24, "sla_blocked": 1},
        "cumulative":  {"date_iso": "", "revenue_cents": 5680, "cost_cents": 2240,
                        "sales_count": 38, "missed_count": 4,
                        "cash_balance_cents": 0, "payable_cents": 0,
                        "audit_count": 91, "sla_blocked": 2},
        "weather_samples": [0.85, 1.15, 1.42, 1.61, 1.30, 0.88],
        "weather_label_today": "晴",
        "season_label_today": "春",
        "holiday_label_today": "工作日",
        "last_report": {
            "date_iso": "2026-04-27",
            "sales_count": 26,  "revenue_cents": 3830, "cost_cents": 1520,
            "profit_cents": 2310, "missed_count": 3,
            "weather_label": "多云", "season_label": "春", "holiday_label": "工作日",
            "avg_traffic": 1.04, "peak_traffic": 1.51,
            "sla_blocked": 1, "audit_count": 67, "payable_delta_cents": 0,
            "next_due_date_iso": "2026-05-31",
            "thought_text": "昨日（2026-04-27・工作日・多云/春）销售 26 单，收入 ¥38，成本 ¥15，利润 ¥23；流失 3，SLA 拦截 1。平均客流 1.04，峰值 1.51。",
        },
        "history": [
            {"date_iso": d, "sales_count": s, "revenue_cents": r, "cost_cents": c,
             "profit_cents": r - c, "missed_count": m,
             "weather_label": w, "season_label": "春", "holiday_label": h,
             "avg_traffic": t, "peak_traffic": t + 0.5, "sla_blocked": 0,
             "audit_count": s * 2, "payable_delta_cents": 0,
             "next_due_date_iso": "2026-05-31",
             "thought_text": f"{d} 销售 {s} 单"}
            for d, s, r, c, m, w, h, t in [
                ("2026-04-27", 26, 3830, 1520, 3, "多云", "工作日", 1.04),
                ("2026-04-26", 31, 4470, 1820, 2, "晴",   "周末",  1.18),
                ("2026-04-25", 33, 4720, 1900, 1, "晴",   "周末",  1.22),
                ("2026-04-24", 22, 3110, 1310, 4, "雨",   "工作日", 0.78),
                ("2026-04-23", 24, 3380, 1430, 3, "多云", "工作日", 0.96),
                ("2026-04-22", 27, 3920, 1610, 2, "晴",   "工作日", 1.10),
                ("2026-04-21", 18, 2640,  990, 5, "雨",   "工作日", 0.65),
            ]
        ],
        "history_cap": 30,
    })

    # Blockchain journal: a healthy stream + 2 stuck intents to demo drift banner.
    journal_lines = [
        {"ts": 1, "kind": "intent_start", "local_uuid": "u1",
         "description": "operate lunch peak", "task_type": "HOURLY",
         "event_spo": ["Vendy Unit-001", "plans", "lunch"],
         "parent_local_uuid": None, "payload_path": ""},
        {"ts": 2, "kind": "tx_start", "local_uuid": "u1",
         "on_chain_id": 12, "tx_hash": "0xa11c0de" + "0" * 56},
        {"ts": 3, "kind": "intent_atomic", "local_uuid": "u2",
         "description": "sold Coke to Marco for $1.58",
         "task_type": "ACTION",
         "event_spo": ["Vendy Unit-001", "sold", "Coke"],
         "parent_local_uuid": "u1", "payload_path": "", "result_path": ""},
        {"ts": 4, "kind": "tx_atomic", "local_uuid": "u2",
         "on_chain_id": 13, "tx_hash": "0xb22d1ef" + "0" * 56},
        {"ts": 5, "kind": "intent_atomic", "local_uuid": "u3",
         "description": "调价 Coke ¥1.50 → ¥1.58", "task_type": "ACTION",
         "event_spo": ["Vendy Unit-001", "set-price", "Coke"],
         "parent_local_uuid": "u1", "payload_path": "", "result_path": ""},
        {"ts": 6, "kind": "tx_atomic", "local_uuid": "u3",
         "on_chain_id": 14, "tx_hash": "0xc33e2fa" + "0" * 56},
        # Stuck: intent_start without tx_start → drift banner shows 1.
        {"ts": 7, "kind": "intent_start", "local_uuid": "stuck-1",
         "description": "RESTOCK_ORDER suntory Coke×24", "task_type": "ACTION",
         "event_spo": ["Vendy Unit-001", "ordered-from", "suntory"],
         "parent_local_uuid": "u1", "payload_path": ""},
    ]
    journal = sim_dir / "blockchain_journal.jsonl"
    journal.write_text("\n".join(json.dumps(line) for line in journal_lines) + "\n")

    # curr_sim_code so /simulator_home would pick this up if user starts reverie.
    temp = REPO_ROOT / "environment" / "frontend_server" / "temp_storage"
    temp.mkdir(exist_ok=True)
    (temp / "curr_sim_code.json").write_text(json.dumps({"sim_code": sim_code}))

    print(f"[seed_demo] wrote sim {sim_code!r} → {sim_dir}")
    print(f"[seed_demo] open http://127.0.0.1:8000/  → enter sim={sim_code} in Live mode")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--sim", default="demo_plaza_run")
    args = parser.parse_args()
    build(args.sim)
