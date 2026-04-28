"""Vending-machine transaction logic.

A purchase decrements inventory, credits cash, and publishes a
`transaction_completed` event so subscribers (e.g. the TaskRegistry adapter)
can record it.
"""

from __future__ import annotations

from dataclasses import dataclass

from .events import bus
from .state import VendingState


class OutOfStock(Exception):
    """Raised when a customer requests a SKU with zero inventory."""


class UnknownSku(Exception):
    """Raised when a customer requests a SKU that Vendy does not carry."""


@dataclass
class Receipt:
    sku: str
    price_cents: int
    customer: str
    timestamp_iso: str


def sell(
    state: VendingState,
    *,
    sku: str,
    customer: str,
    timestamp_iso: str,
    vendy_local_uuid_parent: str | None = None,
) -> Receipt:
    if sku not in state.prices_cents:
        raise UnknownSku(sku)
    if state.inventory.get(sku, 0) <= 0:
        raise OutOfStock(sku)

    price = state.prices_cents[sku]
    state.inventory[sku] -= 1
    state.cash_balance_cents += price

    receipt = Receipt(sku=sku, price_cents=price, customer=customer, timestamp_iso=timestamp_iso)

    bus.publish({
        "kind":        "transaction_completed",
        "persona":     "Vendy Unit-001",
        "sku":         sku,
        "price_cents": price,
        "customer":    customer,
        "timestamp":   timestamp_iso,
        "parent_local_uuid": vendy_local_uuid_parent,
    })
    return receipt
