"""Tests for vending/llm_action_loop.py — prompt build + dispatch path."""

from __future__ import annotations

import datetime as _dt
import sys
import types
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
        sku_unit_costs_cents={"Coke": 60},
    ))
    p.memory = []
    return p


def _stub_openai(reply_text: str):
    """Inject a fake openai module that returns reply_text from create()."""
    captured = {"prompts": []}
    mod = types.ModuleType("openai")

    class _ChatCompletion:
        @staticmethod
        def create(*args, **kwargs):
            captured["prompts"].append(kwargs.get("messages", [{}])[0].get("content", ""))
            return {"choices": [{"message": {"content": reply_text}}]}

    mod.ChatCompletion = _ChatCompletion
    sys.modules["openai"] = mod
    return captured


def test_request_business_action_dispatches_price_change(fresh_bus, monkeypatch):
    captured = _stub_openai('{"action":"PRICE_CHANGE","sku":"Coke","next_price_cents":158}')

    # Reload modules so model_router picks up the stub openai.
    import importlib
    import vending.model_router
    import vending.llm_action_loop
    importlib.reload(vending.model_router)
    importlib.reload(vending.llm_action_loop)

    p = _make_vendy()
    res = vending.llm_action_loop.request_business_action(
        p, now=_dt.datetime(2026, 4, 28, 12, 0, 0))
    assert res.applied is True
    assert "committed" in res.summary
    assert p.vending_state.prices_cents["Coke"] == 158
    # Memory got an audit line.
    assert any("PRICE_CHANGE" in m for m in p.memory)
    # Prompt contained the live state preamble.
    assert "Business state" in captured["prompts"][0]
    assert "Coke=12" in captured["prompts"][0]
    assert "SLA guardrails" in captured["prompts"][0]


def test_request_business_action_handles_no_op(fresh_bus, monkeypatch):
    _stub_openai('{"action":"NO_OP","rationale":"steady state"}')

    import importlib
    import vending.model_router
    import vending.llm_action_loop
    importlib.reload(vending.model_router)
    importlib.reload(vending.llm_action_loop)

    p = _make_vendy()
    res = vending.llm_action_loop.request_business_action(p)
    assert res.applied is False
    assert "no actionable" in res.summary
    # No price change committed.
    assert p.vending_state.prices_cents["Coke"] == 150


def test_request_business_action_swallows_openai_failure(fresh_bus, monkeypatch):
    # Stub openai that raises.
    mod = types.ModuleType("openai")

    class _ChatCompletion:
        @staticmethod
        def create(*args, **kwargs):
            raise RuntimeError("api timeout")

    mod.ChatCompletion = _ChatCompletion
    sys.modules["openai"] = mod

    import importlib
    import vending.model_router
    import vending.llm_action_loop
    importlib.reload(vending.model_router)
    importlib.reload(vending.llm_action_loop)

    p = _make_vendy()
    res = vending.llm_action_loop.request_business_action(p)
    assert res.applied is False
    assert "error" in res.summary
    # State untouched.
    assert p.vending_state.prices_cents["Coke"] == 150


def test_request_business_action_blocks_oversized_jump_via_gate(fresh_bus, monkeypatch):
    _stub_openai('{"action":"PRICE_CHANGE","sku":"Coke","next_price_cents":300}')

    import importlib
    import vending.model_router
    import vending.llm_action_loop
    importlib.reload(vending.model_router)
    importlib.reload(vending.llm_action_loop)

    p = _make_vendy()
    res = vending.llm_action_loop.request_business_action(p)
    assert res.applied is False
    assert "BLOCKED" in res.summary
    assert p.vending_state.prices_cents["Coke"] == 150
    # Memory still records the BLOCKED event.
    assert any("BLOCKED" in m for m in p.memory)
