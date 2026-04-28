"""VendingState load/save and prompt preamble tests."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "reverie" / "backend_server"))


def test_state_round_trip(tmp_path):
    from vending.state import VendingState

    p = tmp_path / "vending_state.json"
    s = VendingState(
        inventory          = {"Coke": 5, "Water": 0},
        prices_cents       = {"Coke": 150, "Water": 100},
        cash_balance_cents = 4200,
        power_pct          = 87.5,
        last_restock_iso   = "2026-04-28T08:00:00",
        wallet_address     = "0xabc",
    )
    s.save(p)

    loaded = VendingState.load(p)
    assert loaded.inventory == {"Coke": 5, "Water": 0}
    assert loaded.prices_cents["Coke"] == 150
    assert loaded.cash_balance_cents == 4200


def test_stock_summary_flags_low(tmp_path):
    from vending.state import VendingState

    s = VendingState(inventory={"Coke": 20, "Water": 2}, prices_cents={"Coke": 150, "Water": 100})
    summary = s.stock_summary(low_threshold=5)
    assert "low: Water" in summary
    assert "Coke=20"   in summary


def test_state_preamble_empty_when_no_state():
    from vending.prompt_inject import state_preamble

    class _P: pass
    assert state_preamble(_P()) == ""


def test_attach_to_persona_appends_preamble_to_daily_plan_req():
    from vending.prompt_inject import attach_to_persona
    from vending.state import VendingState

    class _Scratch:
        def __init__(self, body):
            self._body = body
        def get_str_daily_plan_req(self):
            return self._body

    class _P: pass
    p = _P()
    p.scratch = _Scratch("Restock before each peak.")
    p.vending_state = VendingState(
        inventory    = {"Coke": 5},
        prices_cents = {"Coke": 150},
    )

    attach_to_persona(p)
    out = p.scratch.get_str_daily_plan_req()
    assert "Restock before each peak." in out
    assert "Business state" in out
    assert "Coke=5" in out

    # Idempotent: a second attach must not double-wrap.
    attach_to_persona(p)
    out2 = p.scratch.get_str_daily_plan_req()
    assert out == out2


def test_transaction_decrements_inventory_and_credits_cash(tmp_path):
    from vending.state import VendingState
    from vending.transaction import sell, OutOfStock, UnknownSku
    from vending.events import bus

    bus.reset()
    received = []
    bus.subscribe(lambda e: received.append(e))

    state = VendingState(
        inventory    = {"Coke": 2},
        prices_cents = {"Coke": 150},
    )
    receipt = sell(state, sku="Coke", customer="Marco", timestamp_iso="t")
    assert state.inventory["Coke"] == 1
    assert state.cash_balance_cents == 150
    assert receipt.price_cents == 150
    assert any(e["kind"] == "transaction_completed" for e in received)

    sell(state, sku="Coke", customer="Marco", timestamp_iso="t")
    with pytest.raises(OutOfStock):
        sell(state, sku="Coke", customer="Marco", timestamp_iso="t")
    with pytest.raises(UnknownSku):
        sell(state, sku="Pringles", customer="Marco", timestamp_iso="t")
