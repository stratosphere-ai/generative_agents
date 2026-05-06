"""Tests for vending/environment_cycle.py: simulated + HTTP providers + customer_spawner integration + prompt preamble."""

from __future__ import annotations

import datetime as _dt
import json
import random
import sys
import urllib.error
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "reverie" / "backend_server"))


# ---------- SimulatedWeatherProvider ---------------------------------------


def test_simulated_provider_caches_weather_within_dwell():
    from vending.environment_cycle import SimulatedWeatherProvider

    rng = random.Random(42)
    p = SimulatedWeatherProvider(dwell_hours=6.0, rng=rng)
    t0 = _dt.datetime(2026, 4, 28, 9, 0, 0)
    w0 = p.current(t0)
    w1 = p.current(t0 + _dt.timedelta(hours=2))     # within dwell
    assert w1 is w0


def test_simulated_provider_re_rolls_after_dwell():
    from vending.environment_cycle import SimulatedWeatherProvider, Weather

    # Forced sequence: first roll -> sunny, second -> rain
    rng = random.Random()
    rng.random = iter([0.99, 0.99, 0.05, 0.05]).__next__   # type: ignore[assignment]
    p = SimulatedWeatherProvider(dwell_hours=1.0, rng=rng)
    t0 = _dt.datetime(2026, 4, 28, 9, 0, 0)
    w0 = p.current(t0)
    w1 = p.current(t0 + _dt.timedelta(hours=2))            # past dwell -> re-roll
    assert isinstance(w0, Weather)
    assert isinstance(w1, Weather)


# ---------- EnvironmentCycle / snapshot multipliers ------------------------


def test_snapshot_holiday_boosts_traffic_and_demand():
    from vending.environment_cycle import EnvironmentCycle, SimulatedWeatherProvider, WEATHERS

    class _StubProvider:
        def current(self, now):
            return WEATHERS["sunny"]

    cycle = EnvironmentCycle(provider=_StubProvider())

    # day-of-year that lands on city festival (day % 28 in {6, 7}).
    weekday_jan_6 = _dt.datetime(2026, 1, 6, 12, 0, 0)
    snap = cycle.snapshot(weekday_jan_6)
    assert snap.holiday == "城市节"
    assert snap.is_holiday is True
    expected_traffic = WEATHERS["sunny"].traffic_mult * snap.season.traffic_mult * 1.45
    assert abs(snap.traffic_multiplier - expected_traffic) < 1e-6
    assert abs(snap.demand_multiplier - WEATHERS["sunny"].demand_mult * 1.45) < 1e-6


def test_snapshot_weekend_marks_is_weekend():
    from vending.environment_cycle import EnvironmentCycle, WEATHERS

    class _Stub:
        def current(self, now): return WEATHERS["cloudy"]

    cycle = EnvironmentCycle(provider=_Stub())
    sunday = _dt.datetime(2026, 5, 3, 10, 0, 0)             # 2026-05-03 is a Sunday
    snap = cycle.snapshot(sunday)
    assert snap.is_weekend is True
    assert snap.holiday in ("周末", "城市节")


def test_snapshot_sku_multiplier_picks_water_or_snack():
    from vending.environment_cycle import EnvironmentCycle, WEATHERS

    class _Stub:
        def current(self, now): return WEATHERS["heat"]

    cycle = EnvironmentCycle(provider=_Stub())
    snap = cycle.snapshot(_dt.datetime(2026, 7, 15, 14, 0, 0))   # summer + heat
    assert snap.sku_multiplier("Water")     == WEATHERS["heat"].water_mult * snap.season.water_mult
    assert snap.sku_multiplier("Chips")     == snap.season.snack_mult
    assert snap.sku_multiplier("UnknownSKU") == 1.0


# ---------- HttpWeatherProvider with stubbed fetch -------------------------


def test_http_provider_uses_mapper_then_caches(monkeypatch):
    from vending.environment_cycle import HttpWeatherProvider, WEATHERS

    calls = {"n": 0}
    def fake_fetch(self):
        calls["n"] += 1
        return {"weather": [{"main": "Rain"}], "main": {"temp": 290}}

    monkeypatch.setattr(HttpWeatherProvider, "_fetch_raw", fake_fetch)

    p = HttpWeatherProvider(url="http://example.invalid/weather", ttl_sec=600)
    t = _dt.datetime(2026, 4, 28, 12, 0, 0)
    w0 = p.current(t)
    w1 = p.current(t + _dt.timedelta(seconds=30))   # within TTL
    assert w0 is WEATHERS["rain"]
    assert w1 is WEATHERS["rain"]
    assert calls["n"] == 1


def test_http_provider_falls_back_when_fetch_fails(monkeypatch):
    from vending.environment_cycle import HttpWeatherProvider, SimulatedWeatherProvider, WEATHERS

    def boom(self):
        raise urllib.error.URLError("network down")

    monkeypatch.setattr(HttpWeatherProvider, "_fetch_raw", boom)

    class _PinSunny(SimulatedWeatherProvider):
        def current(self, now): return WEATHERS["sunny"]

    p = HttpWeatherProvider(
        url="http://example.invalid/weather",
        fallback=_PinSunny(),
    )
    w = p.current(_dt.datetime(2026, 4, 28, 12, 0, 0))
    assert w is WEATHERS["sunny"]


def test_http_provider_default_mapper_handles_extreme_temp():
    from vending.environment_cycle import default_openweathermap_mapper, WEATHERS

    cold = default_openweathermap_mapper({"weather": [{"main": "Clear"}], "main": {"temp": 270}})
    hot  = default_openweathermap_mapper({"weather": [{"main": "Clear"}], "main": {"temp": 308}})
    rain = default_openweathermap_mapper({"weather": [{"main": "Drizzle"}], "main": {"temp": 290}})
    assert cold is WEATHERS["cold"]
    assert hot  is WEATHERS["heat"]
    assert rain is WEATHERS["rain"]


# ---------- customer_spawner integration -----------------------------------


def test_expected_arrivals_scales_with_traffic_multiplier():
    from vending.customer_spawner import expected_arrivals
    from vending.environment_cycle import EnvironmentSnapshot, WEATHERS, SEASONS

    # Force every random.random() call to return 0.4 so the gate is purely about
    # whether prob > 0.4.
    class _FixedRng:
        def random(self): return 0.4

    sun_snap = EnvironmentSnapshot(
        now=_dt.datetime(2026, 5, 1, 12, 0, 0),
        weather=WEATHERS["sunny"], season=SEASONS[0], holiday="城市节",
        weekday=5, hour=12, is_weekend=False, is_holiday=True,
    )
    rain_snap = EnvironmentSnapshot(
        now=_dt.datetime(2026, 5, 1, 12, 0, 0),
        weather=WEATHERS["rain"],  season=SEASONS[0], holiday="工作日",
        weekday=5, hour=12, is_weekend=False, is_holiday=False,
    )
    sun_arrivals  = expected_arrivals(12, rng=_FixedRng(), env=sun_snap)
    rain_arrivals = expected_arrivals(12, rng=_FixedRng(), env=rain_snap)
    # Holiday + sunny lifts everyone above the 0.4 gate; rain drops them under.
    assert len(sun_arrivals) >= len(rain_arrivals)


# ---------- prompt preamble -----------------------------------------------


def test_state_preamble_includes_environment_section():
    from vending.prompt_inject import state_preamble
    from vending.state import VendingState
    from vending.environment_cycle import EnvironmentCycle, SimulatedWeatherProvider

    class _P: pass
    p = _P()
    p.vending_state = VendingState(inventory={"Coke": 5}, prices_cents={"Coke": 150})
    p.env_snapshot  = EnvironmentCycle(provider=SimulatedWeatherProvider(rng=random.Random(0))).snapshot(
        _dt.datetime(2026, 4, 28, 12, 0, 0))

    out = state_preamble(p)
    assert "## Environment" in out
    assert "天气" in out
    assert "季节" in out
