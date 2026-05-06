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
