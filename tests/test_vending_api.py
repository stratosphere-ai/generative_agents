"""Smoke tests for the /api/vending/* HTTP endpoints.

We avoid spinning up the whole Django test client (which requires settings
init + DB) by calling the view functions directly with a stub request that
exposes only the attributes the views read.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
FRONTEND  = REPO_ROOT / "environment" / "frontend_server"


class _StubRequest:
    def __init__(self, method="GET", **query):
        self.method = method
        self.GET    = query

    def __repr__(self):
        return f"<StubRequest {self.method} GET={self.GET}>"


@pytest.fixture()
def vending_views(tmp_path, monkeypatch):
    """Import views.py with cwd=frontend_server and a Django settings stub in place."""
    monkeypatch.chdir(FRONTEND)

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "frontend_server.settings.base")
    sys.path.insert(0, str(FRONTEND))
    import django                                       # type: ignore[import-not-found]
    if not getattr(django, "_setup_done", False):
        try:
            django.setup()
            django._setup_done = True
        except Exception:
            pass

    if "translator.vending_api" in sys.modules:
        del sys.modules["translator.vending_api"]
    from translator import vending_api as v
    return v


@pytest.fixture()
def fake_sim(tmp_path, monkeypatch):
    """Build a sim folder that the views can read."""
    monkeypatch.chdir(FRONTEND)
    sim_code = "vending_test_sim"
    sim_dir  = FRONTEND / "storage" / sim_code

    if sim_dir.exists():
        # Clean any leftover from a prior aborted run.
        import shutil; shutil.rmtree(sim_dir)

    (sim_dir / "reverie").mkdir(parents=True)
    (sim_dir / "personas" / "Vendy Unit-001" / "bootstrap_memory").mkdir(parents=True)
    (sim_dir / "personas" / "Marco Silva"   / "bootstrap_memory").mkdir(parents=True)
    (sim_dir / "environment").mkdir()
    (sim_dir / "task_payloads").mkdir()

    (sim_dir / "reverie" / "meta.json").write_text(json.dumps({
        "maze_name":     "vending",
        "curr_time":     "April 28, 2026, 09:00:00",
        "step":          12,
        "persona_names": ["Vendy Unit-001", "Marco Silva"],
    }))
    (sim_dir / "personas" / "Vendy Unit-001" / "bootstrap_memory" / "vending_state.json").write_text(json.dumps({
        "inventory": {"Coke": 18, "Water": 12},
        "prices_cents": {"Coke": 150, "Water": 100},
        "cash_balance_cents": 450,
        "power_pct": 87.5,
    }))
    (sim_dir / "personas" / "Vendy Unit-001" / "bootstrap_memory" / "sla_state.json").write_text(json.dumps({
        "enabled": True, "blocked": 3, "claims": 1, "risk_limit": 72,
        "max_loan_cents": 14000, "max_order_cost_cents": 4800, "max_price_change": 0.45,
    }))
    (sim_dir / "personas" / "Vendy Unit-001" / "bootstrap_memory" / "price_governance.json").write_text(json.dumps({
        "min_interval_hours": 12, "max_daily_changes": 1, "max_single_change_rate": 0.12,
        "last_changed": {"Coke": "2026-04-28T08:00:00"},
        "changes_today": {"Coke": 1},
        "today_iso_date": "2026-04-28",
        "last_action": "Coke 150 -> 158",
    }))
    (sim_dir / "personas" / "Vendy Unit-001" / "bootstrap_memory" / "supplier_ledger.json").write_text(json.dumps({
        "_seq": 5,
        "contracts": {"suntory": {
            "supplier_id": "suntory", "label": "サントリー",
            "cutoff_day": 31, "payment_offset_months": 1, "payment_day": 31,
            "consumption_tax_rate": 0.08, "transfer_fee_cents_buyer": 220,
            "early_payment_discount": 0.0, "sku_unit_costs_cents": {"Coke": 60},
        }},
        "open_orders": [],
        "invoices": [{
            "invoice_id": "INV-00001", "supplier_id": "suntory",
            "cutoff_date": "2026-04-30", "due_date": "2026-05-31",
            "lines": [{"sku": "Coke", "quantity": 24, "unit_cost_cents": 60}],
            "subtotal_cents": 1440, "tax_cents": 115, "transfer_fee_cents": 220,
            "total_cents": 1775, "paid": False, "paid_date": None,
        }],
        "history": [],
    }))
    (sim_dir / "personas" / "Vendy Unit-001" / "bootstrap_memory" / "daily_report.json").write_text(json.dumps({
        "today_date_iso": None,
        "today_start": None,
        "cumulative": {"date_iso": "", "revenue_cents": 600, "cost_cents": 240,
                        "sales_count": 5, "missed_count": 1,
                        "cash_balance_cents": 0, "payable_cents": 0,
                        "audit_count": 8, "sla_blocked": 1},
        "weather_samples": [],
        "weather_label_today": "晴", "season_label_today": "春", "holiday_label_today": "工作日",
        "last_report": {
            "date_iso": "2026-04-28",
            "sales_count": 5, "revenue_cents": 600, "cost_cents": 240,
            "profit_cents": 360, "missed_count": 1,
            "weather_label": "晴", "season_label": "春", "holiday_label": "工作日",
            "avg_traffic": 1.2, "peak_traffic": 1.6,
            "sla_blocked": 1, "audit_count": 8, "payable_delta_cents": 0,
            "next_due_date_iso": "2026-05-31",
            "thought_text": "昨日 销售 5 单。",
        },
        "history": [],
        "history_cap": 30,
    }))
    (sim_dir / "personas" / "Vendy Unit-001" / "bootstrap_memory" / "scratch.json").write_text(json.dumps({
        "name": "Vendy Unit-001", "daily_plan_req": "operate as vendor"
    }))
    (sim_dir / "personas" / "Marco Silva" / "bootstrap_memory" / "scratch.json").write_text(json.dumps({
        "name": "Marco Silva", "role": "customer", "daily_plan_req": "stop by plaza"
    }))
    (sim_dir / "environment" / "12.json").write_text(json.dumps({
        "Vendy Unit-001": {"maze": "vending", "x": 5,  "y": 10},
        "Marco Silva":    {"maze": "vending", "x": 25, "y": 18},
    }))
    journal = sim_dir / "blockchain_journal.jsonl"
    journal.write_text("\n".join([
        json.dumps({"ts": 1, "kind": "intent_start",    "local_uuid": "u1", "description": "lunch peak", "task_type": "HOURLY", "event_spo": ["Vendy Unit-001", "plans", "lunch peak"], "parent_local_uuid": None, "payload_path": ""}),
        json.dumps({"ts": 2, "kind": "tx_start",        "local_uuid": "u1", "on_chain_id": 1, "tx_hash": "0xaaa"}),
        json.dumps({"ts": 3, "kind": "intent_complete", "local_uuid": "u1", "result_path": ""}),
        json.dumps({"ts": 4, "kind": "tx_complete",     "local_uuid": "u1", "on_chain_id": 1, "tx_hash": "0xbbb"}),
    ]))
    return sim_code


def test_vending_state_api_returns_state_and_meta(vending_views, fake_sim):
    resp = vending_views.vending_state_api(_StubRequest(), fake_sim)
    body = json.loads(resp.content)

    assert resp.status_code == 200
    assert body["sim_code"]     == fake_sim
    assert body["maze_name"]    == "vending"
    assert body["step"]         == 12
    assert body["vending_state"]["__persona"] == "Vendy Unit-001"
    assert body["vending_state"]["inventory"]["Coke"] == 18
    assert body["vending_state"]["cash_balance_cents"] == 450


def test_vending_state_api_404_on_unknown_sim(vending_views):
    resp = vending_views.vending_state_api(_StubRequest(), "no_such_sim_xyz")
    assert resp.status_code == 404


def test_vending_state_api_rejects_path_traversal(vending_views):
    resp = vending_views.vending_state_api(_StubRequest(), "../etc")
    assert resp.status_code == 404


def test_vending_journal_api_returns_entries_and_supports_since(vending_views, fake_sim):
    resp = vending_views.vending_journal_api(_StubRequest(), fake_sim)
    body = json.loads(resp.content)
    assert body["count"]   == 4
    assert body["entries"][0]["kind"] == "intent_start"
    assert body["entries"][1]["tx_hash"] == "0xaaa"

    resp2 = vending_views.vending_journal_api(_StubRequest(since="2"), fake_sim)
    body2 = json.loads(resp2.content)
    assert body2["since"] == 2
    assert body2["count"] == 2
    assert body2["entries"][0]["kind"] == "intent_complete"


def test_vending_personas_api_returns_positions_and_role(vending_views, fake_sim):
    resp = vending_views.vending_personas_api(_StubRequest(), fake_sim)
    body = json.loads(resp.content)
    assert body["step"] == 12
    by_name = {p["name"]: p for p in body["personas"]}
    assert by_name["Marco Silva"]["x"]    == 25
    assert by_name["Marco Silva"]["role"] == "customer"
    assert by_name["Vendy Unit-001"]["x"] == 5


def test_vending_sla_api_returns_state(vending_views, fake_sim):
    resp = vending_views.vending_sla_api(_StubRequest(), fake_sim)
    body = json.loads(resp.content)
    assert body["sla_state"]["blocked"]    == 3
    assert body["sla_state"]["risk_limit"] == 72
    assert body["sla_state"]["__persona"]  == "Vendy Unit-001"


def test_vending_governance_api_returns_state(vending_views, fake_sim):
    resp = vending_views.vending_governance_api(_StubRequest(), fake_sim)
    body = json.loads(resp.content)
    assert body["price_governance"]["max_daily_changes"] == 1
    assert body["price_governance"]["last_action"] == "Coke 150 -> 158"


def test_vending_supplier_api_returns_payable_and_next_due(vending_views, fake_sim):
    resp = vending_views.vending_supplier_api(_StubRequest(), fake_sim)
    body = json.loads(resp.content)
    assert body["payable_total_cents"] == 1775
    assert body["next_due"]["due_date"] == "2026-05-31"
    assert "suntory" in body["contracts"]


def test_vending_daily_report_api_returns_last_report(vending_views, fake_sim):
    resp = vending_views.vending_daily_report_api(_StubRequest(), fake_sim)
    body = json.loads(resp.content)
    assert body["last_report"]["sales_count"] == 5
    assert body["last_report"]["profit_cents"] == 360
    assert body["weather_today"]["weather"] == "晴"


def test_vending_daily_report_api_404_on_unknown_sim(vending_views):
    resp = vending_views.vending_daily_report_api(_StubRequest(), "no_such_sim_xyz")
    assert resp.status_code == 404


def test_vending_reconcile_api_summarizes_drift(vending_views, fake_sim):
    # The fake_sim journal has 1 clean intent (u1) only; no drift.
    resp = vending_views.vending_reconcile_api(_StubRequest(), fake_sim)
    body = json.loads(resp.content)
    assert body["total_local_uuids"] == 1
    assert body["drift_count"]       == 0
    assert body["has_drift"]         is False


def test_vending_reconcile_api_flags_pending_intents(vending_views, fake_sim, tmp_path):
    # Append two stuck intents to the existing journal (lead with \n in case
    # the original write didn't terminate the last line).
    journal = FRONTEND / "storage" / fake_sim / "blockchain_journal.jsonl"
    with journal.open("a") as f:
        f.write("\n" + json.dumps({"ts": 9, "kind": "intent_start", "local_uuid": "stuck1",
                            "description": "stuck", "task_type": "ACTION"}) + "\n")
        f.write(json.dumps({"ts": 10, "kind": "intent_start", "local_uuid": "stuck2",
                            "description": "also stuck", "task_type": "ACTION"}) + "\n")
        f.write(json.dumps({"ts": 11, "kind": "tx_start", "local_uuid": "stuck2",
                            "on_chain_id": 5, "tx_hash": "0xfff"}) + "\n")

    resp = vending_views.vending_reconcile_api(_StubRequest(), fake_sim)
    body = json.loads(resp.content)
    assert body["has_drift"]    is True
    assert body["drift_count"]  == 1                # only stuck1 has no tx_start
    assert "stuck1" in body["unsubmitted_intents"]
    assert "stuck2" not in body["unsubmitted_intents"]
