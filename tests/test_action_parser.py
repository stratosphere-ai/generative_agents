"""Tests for vending/action_parser.py — LLM output → ParsedAction → dispatch."""

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


# ---- parse_action ---------------------------------------------------------


def test_parse_fenced_json_block():
    from vending.action_parser import parse_action
    text = """
    Reasoning: lunch peak coming.
    ```json
    {"action": "PRICE_CHANGE", "sku": "Coke", "next_price_cents": 158}
    ```
    """
    p = parse_action(text)
    assert p.action == "PRICE_CHANGE"
    assert p.payload["sku"] == "Coke"
    assert p.payload["next_price_cents"] == 158


def test_parse_inline_json_with_surrounding_prose():
    from vending.action_parser import parse_action
    text = "I think we should do {\"action\":\"PRICE_CHANGE\",\"sku\":\"Coke\",\"next_price_cents\":160} now."
    p = parse_action(text)
    assert p.action == "PRICE_CHANGE"
    assert p.payload["next_price_cents"] == 160


def test_parse_yen_field_normalizes_to_cents():
    from vending.action_parser import parse_action
    p = parse_action({"action": "PRICE_CHANGE", "sku": "Coke", "next_price_yen": 1.58})
    assert p.payload["next_price_cents"] == 158


def test_parse_restock_items_dict_form():
    from vending.action_parser import parse_action
    p = parse_action({
        "action": "RESTOCK_ORDER",
        "supplier_id": "suntory",
        "items": [{"sku": "Coke", "qty": 12}, {"sku": "Water", "qty": 6}],
    })
    assert p.payload["items"] == [("Coke", 12), ("Water", 6)]


def test_parse_no_op_returns_none():
    from vending.action_parser import parse_action
    assert parse_action({"action": "NO_OP"}) is None


def test_parse_garbage_returns_none():
    from vending.action_parser import parse_action
    assert parse_action("Just thinking out loud, no JSON here.") is None
    assert parse_action("") is None
    assert parse_action("{this is not valid json}") is None


def test_parse_first_valid_blob_among_multiple_braces():
    from vending.action_parser import parse_action
    text = '{not valid} and then {"action":"PRICE_CHANGE","sku":"Coke","next_price_cents":155}'
    p = parse_action(text)
    assert p.action == "PRICE_CHANGE"
    assert p.payload["sku"] == "Coke"


# ---- dispatch -------------------------------------------------------------


def test_dispatch_price_change_commits_when_allowed(fresh_bus):
    from vending.action_parser import dispatch
    p = _make_vendy()
    text = '{"action":"PRICE_CHANGE","sku":"Coke","next_price_cents":158,"rationale":"peak"}'
    assess, applied, summary = dispatch(p, text, now=_dt.datetime(2026, 4, 28, 12, 0, 0))
    assert assess.allowed is True
    assert applied is True
    assert "committed" in summary
    assert p.vending_state.prices_cents["Coke"] == 158


def test_dispatch_price_change_blocked_when_step_too_large(fresh_bus):
    from vending.action_parser import dispatch
    p = _make_vendy()
    text = '{"action":"PRICE_CHANGE","sku":"Coke","next_price_cents":250}'
    assess, applied, summary = dispatch(p, text, now=_dt.datetime(2026, 4, 28, 12, 0, 0))
    assert assess.allowed is False
    assert applied is False
    assert "BLOCKED" in summary
    assert "保单限制" in summary
    assert p.vending_state.prices_cents["Coke"] == 150


def test_dispatch_restock_commits_when_allowed(fresh_bus):
    from vending.action_parser import dispatch
    p = _make_vendy()
    text = '{"action":"RESTOCK_ORDER","supplier_id":"suntory","items":[["Coke",12]]}'
    assess, applied, summary = dispatch(p, text, now=_dt.datetime(2026, 4, 28, 12, 0, 0))
    assert assess.allowed is True
    assert applied is True
    assert len(p.supplier_ledger.open_orders) == 1


def test_dispatch_restock_unknown_supplier_returns_error_summary(fresh_bus):
    from vending.action_parser import dispatch
    p = _make_vendy()
    text = '{"action":"RESTOCK_ORDER","supplier_id":"unknown","items":[["Coke",1]]}'
    assess, applied, summary = dispatch(p, text)
    assert assess is None
    assert applied is False
    assert "unknown" in summary or "rejected" in summary


def test_dispatch_no_action_returns_no_op(fresh_bus):
    from vending.action_parser import dispatch
    p = _make_vendy()
    assess, applied, summary = dispatch(p, "Just thinking, no action.")
    assert assess is None
    assert applied is False
    assert "no actionable" in summary


def test_dispatch_missing_required_fields_returns_error(fresh_bus):
    from vending.action_parser import dispatch
    p = _make_vendy()
    assess, applied, summary = dispatch(p, '{"action":"PRICE_CHANGE","sku":"Coke"}')
    assert assess is None
    assert applied is False
    assert "missing" in summary
