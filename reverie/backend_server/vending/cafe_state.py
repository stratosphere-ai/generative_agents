"""Static cafe state, the on-square competitor for Vendy.

Not a persona — just a cheap data object so customer plans have a real
alternative to choose from when deciding whether to buy from Vendy.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CafeState:
    open_hour: int = 7
    close_hour: int = 22
    prices_cents: dict[str, int] = field(default_factory=lambda: {
        "Coffee": 350,
        "Water":  150,
        "Pastry": 280,
    })

    def is_open(self, hour: int) -> bool:
        return self.open_hour <= hour < self.close_hour
