"""Lightweight customer-traffic shaper for Central Plaza.

Three persona customers (Lin Wei, Marco Silva, Hana Park) live in the sim;
this spawner decides at each tick whether one of them should walk on-screen.

Spawn probability is the persona's hour PDF scaled by the live environment
(weather × season × holiday) snapshot when an `EnvironmentCycle` is passed.
"""

from __future__ import annotations

import datetime as _dt
import random
from dataclasses import dataclass
from typing import Optional

from .environment_cycle import EnvironmentCycle, EnvironmentSnapshot


PEAKS = (
    (8, 9, +1),    # morning commute
    (12, 13, +1), # lunch
    (18, 19, +1), # evening stroll
)
BASELINE_PER_HOUR = 1


@dataclass(frozen=True)
class CustomerIntent:
    persona_name: str
    sku_preference: tuple[str, ...]
    arrival_hour_pdf: dict[int, float]


PROFILES: dict[str, CustomerIntent] = {
    "Lin Wei":     CustomerIntent("Lin Wei",     ("Water", "Coffee"),    {8: 0.7, 12: 0.5, 18: 0.6}),
    "Marco Silva": CustomerIntent("Marco Silva", ("Coke", "Chocolate"),  {12: 0.9, 13: 0.7}),
    "Hana Park":   CustomerIntent("Hana Park",   ("Water",),             {18: 0.8, 19: 0.5}),
}


def expected_arrivals(
    hour: int,
    *,
    rng: random.Random | None = None,
    env: EnvironmentSnapshot | None = None,
) -> list[str]:
    """Sample which customer personas (if any) should arrive at the plaza this hour.

    When `env` is provided each persona's base probability is multiplied by
    `env.traffic_multiplier` (capped at 1.0) so weather and holidays bend the
    distribution.
    """
    rng = rng or random
    traffic_mult = env.traffic_multiplier if env is not None else 1.0
    arrivals: list[str] = []
    for name, profile in PROFILES.items():
        prob = profile.arrival_hour_pdf.get(hour, 0.05) * traffic_mult
        prob = max(0.0, min(1.0, prob))
        if rng.random() < prob:
            arrivals.append(name)
    return arrivals


def expected_arrivals_for_now(
    now: _dt.datetime,
    cycle: EnvironmentCycle,
    *,
    rng: random.Random | None = None,
) -> tuple[list[str], EnvironmentSnapshot]:
    """Convenience: take a wall-clock `now` and an EnvironmentCycle, return both."""
    snapshot = cycle.snapshot(now)
    return expected_arrivals(now.hour, rng=rng, env=snapshot), snapshot
