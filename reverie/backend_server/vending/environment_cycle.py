"""Environment cycle: weather + season + holiday → traffic / demand multipliers.

Two-layer design so the sim can run offline today and switch to a real
meteorology feed later without rewriting consumers:

    WeatherProvider (Protocol)
      ├── SimulatedWeatherProvider   # default, ported from index.html
      └── HttpWeatherProvider        # talks to a real API (OpenWeatherMap,
                                     # QWeather, NWS, ...) via a user-supplied
                                     # fetcher + mapper. Falls back to the
                                     # simulated provider on error so live
                                     # sims never crash on a hiccup.

`EnvironmentCycle` ties one provider to a deterministic season + holiday
calendar and exposes a single `snapshot(now)` for callers
(`customer_spawner`, `prompt_inject`).
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import random
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol


# ---------------------------------------------------------------------------
# Static reference data (mirrors index.html constants exactly)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Weather:
    id:           str
    label:        str
    demand_mult:  float
    traffic_mult: float
    water_mult:   float


WEATHERS: dict[str, Weather] = {
    w.id: w for w in [
        Weather("sunny",  "晴",   demand_mult=1.08, traffic_mult=1.05, water_mult=1.12),
        Weather("cloudy", "多云", demand_mult=1.00, traffic_mult=1.00, water_mult=1.00),
        Weather("rain",   "雨",   demand_mult=0.76, traffic_mult=0.58, water_mult=0.78),
        Weather("heat",   "高温", demand_mult=1.24, traffic_mult=0.92, water_mult=1.55),
        Weather("cold",   "寒冷", demand_mult=0.90, traffic_mult=0.82, water_mult=0.70),
    ]
}


@dataclass(frozen=True)
class Season:
    id:           str
    label:        str
    traffic_mult: float
    water_mult:   float
    snack_mult:   float


SEASONS: list[Season] = [
    Season("spring", "春", traffic_mult=1.08, water_mult=1.00, snack_mult=1.00),
    Season("summer", "夏", traffic_mult=1.12, water_mult=1.40, snack_mult=0.96),
    Season("autumn", "秋", traffic_mult=1.02, water_mult=0.95, snack_mult=1.08),
    Season("winter", "冬", traffic_mult=0.82, water_mult=0.74, snack_mult=1.03),
]


# ---------------------------------------------------------------------------
# Holiday + season calendars (deterministic — same input → same output)
# ---------------------------------------------------------------------------

def holiday_for(day: int, weekday: int) -> str:
    """Match index.html: day 6/7 of every 28-day cycle = 城市节, sat/sun = 周末."""
    if day % 28 in (6, 7):
        return "城市节"
    if weekday in (0, 6):
        return "周末"
    return "工作日"


def season_for(day: int) -> Season:
    return SEASONS[((day - 1) // 7) % len(SEASONS)]


# ---------------------------------------------------------------------------
# Provider interface
# ---------------------------------------------------------------------------

class WeatherProvider(Protocol):
    def current(self, now: _dt.datetime) -> Weather: ...


# ----- Simulated -----------------------------------------------------------


class SimulatedWeatherProvider:
    """Random-walk weather seeded by date + an optional rng.

    Re-rolls every `dwell_hours` of in-sim time. Distribution depends on the
    active season (summer biased toward heat/rain, winter toward cold/cloudy).
    """

    def __init__(self, *, dwell_hours: float = 6.0, rng: random.Random | None = None) -> None:
        self.dwell_hours  = dwell_hours
        self._rng         = rng or random.Random()
        self._last_roll_h: float | None = None
        self._current     = WEATHERS["sunny"]

    def current(self, now: _dt.datetime) -> Weather:
        elapsed_h = now.timestamp() / 3600.0
        if self._last_roll_h is None or (elapsed_h - self._last_roll_h) >= self.dwell_hours:
            self._current = self._roll(season_for(now.timetuple().tm_yday))
            self._last_roll_h = elapsed_h
        return self._current

    def _roll(self, season: Season) -> Weather:
        r = self._rng.random()
        if season.id == "summer":
            if r < 0.34: return WEATHERS["heat"]
            if r < 0.58: return WEATHERS["rain"]
        elif season.id == "winter":
            if r < 0.38: return WEATHERS["cold"]
            if r < 0.55: return WEATHERS["cloudy"]
        if r < 0.20: return WEATHERS["rain"]
        if r < 0.48: return WEATHERS["cloudy"]
        return WEATHERS["sunny"]


# ----- HTTP (real API placeholder) -----------------------------------------


WeatherMapper = Callable[[Mapping[str, Any]], Weather]


def default_openweathermap_mapper(payload: Mapping[str, Any]) -> Weather:
    """Map an OpenWeatherMap /weather response into our 5-bucket Weather.

    Extend / replace via the `mapper` parameter when wiring a different API
    (QWeather, NWS, China Meteorological Administration, ...).
    """
    main: str = (payload.get("weather") or [{}])[0].get("main", "Clear")
    main_l = main.lower()
    temp_k = payload.get("main", {}).get("temp", 285)
    temp_c = temp_k - 273.15 if temp_k > 100 else temp_k     # accept either unit

    if "rain" in main_l or "drizzle" in main_l or "thunder" in main_l:
        return WEATHERS["rain"]
    if "snow" in main_l or temp_c < 5:
        return WEATHERS["cold"]
    if temp_c > 30:
        return WEATHERS["heat"]
    if "cloud" in main_l or "fog" in main_l or "mist" in main_l:
        return WEATHERS["cloudy"]
    return WEATHERS["sunny"]


class HttpWeatherProvider:
    """Real-API weather provider with TTL cache + safe fallback.

    Wiring example (set in env or a config file):

        WEATHER_PROVIDER=openweathermap
        WEATHER_API_URL=https://api.openweathermap.org/data/2.5/weather?lat=31.23&lon=121.47&appid=$KEY
        WEATHER_TTL_SEC=600

    Then at sim start:

        from vending.environment_cycle import HttpWeatherProvider, default_openweathermap_mapper
        provider = HttpWeatherProvider(
            url=os.environ["WEATHER_API_URL"],
            mapper=default_openweathermap_mapper,
            ttl_sec=int(os.environ.get("WEATHER_TTL_SEC", "600")),
        )

    Tests stub `_fetch_raw` so they don't touch the network.
    """

    def __init__(
        self,
        *,
        url:     str,
        mapper:  WeatherMapper = default_openweathermap_mapper,
        ttl_sec: int = 600,
        fallback: WeatherProvider | None = None,
        timeout_sec: float = 5.0,
    ) -> None:
        self.url      = url
        self.mapper   = mapper
        self.ttl_sec  = ttl_sec
        self.fallback = fallback or SimulatedWeatherProvider()
        self.timeout_sec = timeout_sec
        self._cached: Weather | None = None
        self._cached_at_epoch: float = 0.0

    def current(self, now: _dt.datetime) -> Weather:
        epoch = now.timestamp()
        if self._cached is not None and (epoch - self._cached_at_epoch) < self.ttl_sec:
            return self._cached
        try:
            payload = self._fetch_raw()
            weather = self.mapper(payload)
            self._cached = weather
            self._cached_at_epoch = epoch
            return weather
        except Exception:                                    # noqa: BLE001 -- any failure → fallback
            if self._cached is not None:
                # Stale-but-better-than-fallback: keep the last value for one TTL.
                return self._cached
            return self.fallback.current(now)

    def _fetch_raw(self) -> Mapping[str, Any]:
        with urllib.request.urlopen(self.url, timeout=self.timeout_sec) as resp:    # noqa: S310
            return json.loads(resp.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# EnvironmentCycle: bind a provider to time + emit traffic / demand multipliers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EnvironmentSnapshot:
    now:        _dt.datetime
    weather:    Weather
    season:     Season
    holiday:    str
    weekday:    int
    hour:       int
    is_weekend: bool
    is_holiday: bool

    @property
    def traffic_multiplier(self) -> float:
        boost = 1.45 if self.is_holiday else 1.0
        return self.weather.traffic_mult * self.season.traffic_mult * boost

    @property
    def demand_multiplier(self) -> float:
        boost = 1.45 if self.is_holiday else 1.0
        return self.weather.demand_mult * boost

    def sku_multiplier(self, sku: str) -> float:
        """Per-SKU demand boost: water in heat / summer, snacks in autumn / winter."""
        s = sku.lower()
        if s in ("water", "coke", "coffee"):
            return self.weather.water_mult * self.season.water_mult
        if s in ("chips", "chocolate"):
            return self.season.snack_mult
        return 1.0

    def summary_lines(self) -> list[str]:
        return [
            f"- 天气：{self.weather.label}（traffic ×{self.weather.traffic_mult:.2f}, demand ×{self.weather.demand_mult:.2f}）",
            f"- 季节：{self.season.label}；今日：{self.holiday}",
            f"- 综合 traffic ×{self.traffic_multiplier:.2f}，demand ×{self.demand_multiplier:.2f}",
        ]


class EnvironmentCycle:
    """Wraps a WeatherProvider and exposes deterministic season/holiday."""

    def __init__(self, *, provider: WeatherProvider | None = None) -> None:
        self.provider = provider or SimulatedWeatherProvider()

    def snapshot(self, now: _dt.datetime) -> EnvironmentSnapshot:
        weather  = self.provider.current(now)
        # day-of-year is enough for season; for holiday we use 1-indexed day-of-month-ish.
        day_idx  = now.timetuple().tm_yday
        weekday  = now.weekday() if hasattr(now, "weekday") else now.timetuple().tm_wday
        # Convert Python's Monday=0..Sunday=6 to JS-style Sunday=0..Saturday=6
        # so the calendar stays compatible with index.html behaviour.
        weekday_js = (weekday + 1) % 7
        holiday  = holiday_for(day_idx, weekday_js)
        season   = season_for(day_idx)
        return EnvironmentSnapshot(
            now        = now,
            weather    = weather,
            season     = season,
            holiday    = holiday,
            weekday    = weekday_js,
            hour       = now.hour,
            is_weekend = weekday_js in (0, 6),
            is_holiday = holiday == "城市节",
        )


def from_env(default_provider: WeatherProvider | None = None) -> EnvironmentCycle:
    """Build an EnvironmentCycle based on environment variables.

    Recognized:
        WEATHER_PROVIDER  = "sim" | "http" (default: sim)
        WEATHER_API_URL   = required when http
        WEATHER_TTL_SEC   = optional, default 600
    """
    provider_kind = os.environ.get("WEATHER_PROVIDER", "sim").lower()
    if provider_kind == "http":
        url = os.environ.get("WEATHER_API_URL")
        if not url:
            return EnvironmentCycle(provider=default_provider)
        return EnvironmentCycle(provider=HttpWeatherProvider(
            url     = url,
            ttl_sec = int(os.environ.get("WEATHER_TTL_SEC", "600")),
        ))
    return EnvironmentCycle(provider=default_provider)
