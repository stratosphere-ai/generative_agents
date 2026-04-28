"""Prefix VendingState facts onto Vendy's plan/reflect prompts.

We do NOT touch any prompt template. Instead, the persona module that owns
the prompt call (e.g. `cognitive_modules/plan.py`'s `_long_term_planning`,
`reflect.py`) calls `state_preamble(persona)` and prepends the returned text
to the prompt body. If the persona has no `vending_state` attached the
preamble is empty and the call is a no-op.
"""

from __future__ import annotations

from typing import Any


def state_preamble(persona: Any) -> str:
    state = getattr(persona, "vending_state", None)
    if state is None:
        return ""
    lines = [
        "## Business state (you, the vending machine)",
        f"- {state.stock_summary()}",
        f"- {state.price_summary()}",
        f"- cash on hand: ${state.cash_balance_cents / 100:.2f}",
        f"- power: {state.power_pct:.0f}%",
    ]
    if state.last_restock_iso:
        lines.append(f"- last restock: {state.last_restock_iso}")
    return "\n".join(lines) + "\n\n"
