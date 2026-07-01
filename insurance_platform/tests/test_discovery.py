from types import SimpleNamespace

import pytest

from app import discovery, llm
from app.discovery import (
    DiscoveredFactor,
    FallbackDiscovery,
    LLMDiscovery,
    RuleDiscovery,
)


def _ship(origin="Shanghai", destination="Rotterdam (Europe)"):
    return SimpleNamespace(origin=origin, destination=destination, route=None, cargo_type=None)


def test_rule_discovery_returns_factors():
    factors = RuleDiscovery().discover(_ship(), 100000)
    assert factors
    for f in factors:
        assert isinstance(f, DiscoveredFactor)
        assert f.source == "rule"
        assert 0 < f.probability < 1
        assert 0 < f.impact <= 1


def test_llm_discovery_parses_and_normalizes(monkeypatch):
    canned = [
        {"key": "Suez Blockage", "label": "Suez closed", "category": "route",
         "probability": 0.2, "impact": 0.15, "search_query": "suez canal closed"},
        {"key": "bad_cat", "label": "Weird", "category": "nonsense",
         "probability": 5, "impact": 9, "search_query": "x"},
    ]
    monkeypatch.setattr(llm, "discover_factors_llm", lambda s, c: canned)
    factors = LLMDiscovery().discover(_ship(), 100000)
    assert len(factors) == 2
    assert all(f.source == "llm" for f in factors)
    assert factors[0].key == "suez_blockage"          # normalized (lowercased, spaces->_)
    assert factors[1].category == "macro"             # invalid category coerced
    assert 0 < factors[1].probability < 1             # clamped
    assert 0 < factors[1].impact <= 0.5               # clamped


def test_llm_discovery_raises_on_empty(monkeypatch):
    monkeypatch.setattr(llm, "discover_factors_llm", lambda s, c: [])
    with pytest.raises(Exception):
        LLMDiscovery().discover(_ship(), 100000)


class _Boom(RuleDiscovery):
    name = "boom"
    def discover(self, shipment_like, cargo_value):
        raise RuntimeError("no api key")


def test_fallback_uses_backup_when_primary_fails():
    fb = FallbackDiscovery(_Boom(), RuleDiscovery())
    factors = fb.discover(_ship(), 100000)
    assert factors and factors[0].source == "rule"


def test_get_discovery_defaults_to_rule(monkeypatch):
    import dataclasses
    # Settings is a frozen dataclass -> swap the whole object on the module.
    monkeypatch.setattr(discovery, "settings",
                        dataclasses.replace(discovery.settings, discovery="rule"))
    assert isinstance(discovery.get_discovery(), RuleDiscovery)
    monkeypatch.setattr(discovery, "settings",
                        dataclasses.replace(discovery.settings, discovery="llm"))
    assert isinstance(discovery.get_discovery(), FallbackDiscovery)
