"""Lightweight customer-traffic shaper for Central Plaza.

Three persona customers (Lin Wei, Marco Silva, Hana Park) live in the sim;
this spawner decides at each tick whether one of them should walk on-screen.

We don't add or remove personas — they are part of the sim. The spawner just
nudges their `daily_plan_req` so Vendy sees realistic foot traffic peaks
(08:00 / 12:00 / 18:00 commute / lunch / stroll).
"""

from __future__ import annotations

import random
from dataclasses import dataclass


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


def expected_arrivals(hour: int, *, rng: random.Random | None = None) -> list[str]:
    """Sample which customer personas (if any) should arrive at the plaza this hour."""
    rng = rng or random
    arrivals: list[str] = []
    for name, profile in PROFILES.items():
        prob = profile.arrival_hour_pdf.get(hour, 0.05)
        if rng.random() < prob:
            arrivals.append(name)
    return arrivals
