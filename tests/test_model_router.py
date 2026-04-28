"""Verify per-persona model routing via with_persona / monkey-patched openai."""

from __future__ import annotations

import sys
import types

import pytest


def _make_openai_stub():
    captured = {"calls": []}
    mod = types.ModuleType("openai")

    class _ChatCompletion:
        @staticmethod
        def create(*args, **kwargs):
            captured["calls"].append(dict(kwargs))
            return {"choices": [{"message": {"content": "ok"}}]}

    mod.ChatCompletion = _ChatCompletion
    return mod, captured


def test_install_routes_to_customer_model_for_customer_persona(monkeypatch):
    fake_openai, captured = _make_openai_stub()
    monkeypatch.setitem(sys.modules, "openai", fake_openai)
    monkeypatch.setenv("MODEL_VENDY",    "vendy-main")
    monkeypatch.setenv("MODEL_CUSTOMER", "customer-cheap")

    # Re-import to pick up the patched env + stub.
    import importlib
    import vending.model_router as mr
    importlib.reload(mr)
    assert mr.install() is True

    class _Scratch: pass
    customer = type("P", (), {})()
    customer.scratch = _Scratch()
    customer.scratch.role = "customer"

    vendy = type("P", (), {})()
    vendy.vending_state = object()  # marks role=vendor

    with mr.with_persona(customer):
        fake_openai.ChatCompletion.create(model="gpt-3.5-turbo", messages=[])
    with mr.with_persona(vendy):
        fake_openai.ChatCompletion.create(model="gpt-3.5-turbo", messages=[])

    assert captured["calls"][0]["model"] == "customer-cheap"
    assert captured["calls"][1]["model"] == "vendy-main"


def test_no_override_outside_with_persona(monkeypatch):
    fake_openai, captured = _make_openai_stub()
    monkeypatch.setitem(sys.modules, "openai", fake_openai)

    import importlib
    import vending.model_router as mr
    importlib.reload(mr)
    mr.install()

    fake_openai.ChatCompletion.create(model="gpt-3.5-turbo", messages=[])
    assert captured["calls"][0]["model"] == "gpt-3.5-turbo"


def test_install_noop_when_openai_missing(monkeypatch):
    monkeypatch.delitem(sys.modules, "openai", raising=False)

    # Pretend openai import fails.
    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__
    def fake_import(name, *args, **kwargs):
        if name == "openai":
            raise ImportError("no openai in test env")
        return real_import(name, *args, **kwargs)
    monkeypatch.setattr("builtins.__import__", fake_import)

    import importlib
    import vending.model_router as mr
    importlib.reload(mr)
    assert mr.install() is False
