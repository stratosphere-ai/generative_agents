"""Persistent business state for the vending-machine persona.

Lives alongside the persona's `scratch.json` in
    storage/<sim>/personas/<vendy>/bootstrap_memory/vending_state.json
and is mounted at runtime as `persona.vending_state`. Kept deliberately
separate from `Scratch` so we don't need to fork the upstream class.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


@dataclass
class VendingState:
    inventory: dict[str, int]              = field(default_factory=dict)
    prices_cents: dict[str, int]           = field(default_factory=dict)
    cash_balance_cents: int                = 0
    power_pct: float                       = 100.0
    last_restock_iso: str | None           = None
    wallet_address: str | None             = None

    @classmethod
    def load(cls, path: Path) -> "VendingState":
        if not path.exists():
            return cls()
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            inventory          = dict(data.get("inventory", {})),
            prices_cents       = dict(data.get("prices_cents", {})),
            cash_balance_cents = int(data.get("cash_balance_cents", 0)),
            power_pct          = float(data.get("power_pct", 100.0)),
            last_restock_iso   = data.get("last_restock_iso"),
            wallet_address     = data.get("wallet_address"),
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), sort_keys=True, indent=2), encoding="utf-8")

    # --- runtime helpers --------------------------------------------------

    def stock_summary(self, *, low_threshold: int = 5) -> str:
        if not self.inventory:
            return "inventory empty"
        parts = [f"{sku}={qty}" for sku, qty in sorted(self.inventory.items())]
        low = [sku for sku, qty in self.inventory.items() if qty <= low_threshold]
        out = "stock " + ", ".join(parts)
        if low:
            out += f" (low: {', '.join(sorted(low))})"
        return out

    def price_summary(self) -> str:
        if not self.prices_cents:
            return "no prices set"
        parts = [f"{sku}=${self.prices_cents[sku] / 100:.2f}" for sku in sorted(self.prices_cents)]
        return "prices " + ", ".join(parts)
