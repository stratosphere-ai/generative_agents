"""SLA + price-governance gate for Vendy's business actions.

Every concrete business decision (price change, restock order, loan request,
repair / clean call) goes through `gate_action` so the same risk-scoring +
audit-emitting logic protects every entry point. On a block the gate

  - calls `sla_state.record_block()` (cumulative counter)
  - publishes a `sla_blocked` bus event so DailyReportListener increments
    today's count
  - returns Assessment(allowed=False, ...)

Two convenience commit helpers wrap the gate for the most common actions:

  - commit_price_change(persona, sku, new_price_cents, now)
  - commit_restock(persona, supplier_id, items, order_date, observation)

If allowed, they apply the side-effect (state mutation + supplier ledger
write) inside the function so the LLM-side glue stays one-line.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any, Mapping

from .events import bus
from .sla import Assessment, SLAState
from .price_governance import PriceGovernance


VENDY = "Vendy Unit-001"


def gate_action(
    persona: Any,
    action: str,
    payload: Mapping[str, Any] | None = None,
    observation: Mapping[str, Any] | None = None,
) -> Assessment:
    """Run an action through SLA assessment; emit and record on a block."""
    sla: SLAState | None = getattr(persona, "sla_state", None)
    if sla is None:
        return Assessment(allowed=True, risk=0, reasons=[])

    payload = dict(payload or {})

    # Price changes also have to clear the per-SKU governance contract.
    if action == "PRICE_CHANGE":
        gov: PriceGovernance | None = getattr(persona, "price_governance", None)
        if gov is not None:
            sku   = payload.get("sku") or ""
            prev  = int(payload.get("previous_price_cents", 0))
            new   = int(payload.get("next_price_cents", prev))
            now   = payload.get("now") or _dt.datetime.now()
            ok, reasons = gov.can_change_price(sku, prev, new, now)
            payload["price_locked"]            = any("观察窗口" in r for r in reasons)
            payload["daily_changes_exhausted"] = any("调价次数已用尽" in r for r in reasons)

    assessment = sla.assess(action, payload, observation)
    if not assessment.allowed:
        sla.record_block()
        bus.publish({
            "kind":    "sla_blocked",
            "persona": VENDY,
            "action":  action,
            "risk":    assessment.risk,
            "reasons": list(assessment.reasons),
        })
    return assessment


def commit_price_change(
    persona: Any,
    *,
    sku: str,
    new_price_cents: int,
    now: _dt.datetime,
) -> Assessment:
    """Gate + apply a price change in one call. Returns the Assessment.

    On allowed=True the side-effects are:
      - persona.vending_state.prices_cents[sku] = new_price_cents
      - persona.price_governance.record_change(sku, prev, new, now)
    On allowed=False nothing is mutated; the bus has the sla_blocked event.
    """
    state = getattr(persona, "vending_state", None)
    if state is None:
        raise RuntimeError("persona has no vending_state")
    prev = int(state.prices_cents.get(sku, 0))

    assessment = gate_action(
        persona, "PRICE_CHANGE",
        payload={
            "sku":                  sku,
            "previous_price_cents": prev,
            "next_price_cents":     new_price_cents,
            "now":                  now,
        },
    )
    if not assessment.allowed:
        return assessment

    state.prices_cents[sku] = int(new_price_cents)
    gov: PriceGovernance | None = getattr(persona, "price_governance", None)
    if gov is not None:
        gov.record_change(sku, prev, int(new_price_cents), now)
    return assessment


def commit_restock(
    persona: Any,
    *,
    supplier_id: str,
    items: list[tuple[str, int]],
    order_date: _dt.date,
    observation: Mapping[str, Any] | None = None,
) -> Assessment:
    """Gate + place a restock order. Returns the Assessment.

    `items` is a list of (sku, quantity) pairs. The gate uses the contract's
    sku_unit_costs_cents to compute the projected order cost so SLA can
    block oversized orders.
    """
    ledger = getattr(persona, "supplier_ledger", None)
    if ledger is None or supplier_id not in ledger.contracts:
        raise RuntimeError(f"no supplier contract for {supplier_id!r}")

    contract = ledger.contracts[supplier_id]
    order_cost = sum(contract.sku_unit_costs_cents.get(sku, 0) * qty for sku, qty in items)

    assessment = gate_action(
        persona, "RESTOCK_ORDER",
        payload={"order_cost_cents": order_cost, "supplier_id": supplier_id},
        observation=observation or {},
    )
    if not assessment.allowed:
        return assessment

    ledger.place_order(supplier_id, items, order_date)
    return assessment
