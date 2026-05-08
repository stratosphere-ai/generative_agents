"""Subscribe to the vending event bus and forward Vendy's actions to TaskRegistry.

Wires the in-process event bus to a TaskRegistryClient (or its mock).
Only events authored by Vendy result in chain writes; customer-side events
update the local state but are not journaled on chain.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping

from .events import bus


log = logging.getLogger(__name__)

VENDY = "Vendy Unit-001"


class TaskRegistryAdapter:
    """Map vending events to TaskRegistry start/complete calls.

    Maintains:
      - `current_hourly_local_uuid`: the local UUID of the in-flight HOURLY parent task.
      - `action_to_local_uuid`: per-action map so action_end can complete the right child.
    """

    def __init__(self, client: Any) -> None:
        self.client = client
        self.current_hourly_local_uuid: str | None = None
        self.action_to_local_uuid: dict[str, str] = {}

    def attach(self) -> None:
        bus.subscribe(self._on_event)

    def detach(self) -> None:
        bus.unsubscribe(self._on_event)

    def _on_event(self, event: Mapping[str, Any]) -> None:
        if event.get("persona") != VENDY:
            return
        kind = event.get("kind")
        try:
            handler = getattr(self, f"_on_{kind}", None)
            if handler is not None:
                handler(event)
        except Exception:                                 # noqa: BLE001
            log.exception("registry adapter failed to handle event %r", kind)

    # --- handlers ---------------------------------------------------------

    def _on_hourly_start(self, e: Mapping[str, Any]) -> None:
        local = self.client.start_task(
            parent_local_uuid = None,
            task_type         = "HOURLY",
            description       = e["description"],
            event_spo         = (VENDY, "plans", e["description"]),
            payload           = {"hour": e.get("hour"), "date": e.get("date")},
        )
        self.current_hourly_local_uuid = local

    def _on_hourly_end(self, e: Mapping[str, Any]) -> None:
        if self.current_hourly_local_uuid is not None:
            self.client.complete_task(
                self.current_hourly_local_uuid,
                {"completed_at": e.get("completed_at")},
            )
            self.current_hourly_local_uuid = None

    def _on_action_start(self, e: Mapping[str, Any]) -> None:
        key = e.get("action_key") or e["description"]
        local = self.client.start_task(
            parent_local_uuid = self.current_hourly_local_uuid,
            task_type         = "ACTION",
            description       = e["description"],
            event_spo         = tuple(e.get("event") or (VENDY, "does", e["description"])),
            payload           = {
                "address":  e.get("address"),
                "duration": e.get("duration"),
            },
        )
        self.action_to_local_uuid[key] = local

    def _on_action_end(self, e: Mapping[str, Any]) -> None:
        key = e.get("action_key") or e["description"]
        local = self.action_to_local_uuid.pop(key, None)
        if local is not None:
            self.client.complete_task(local, {"completed_at": e.get("completed_at")})

    def _atomic_or_pair(self, **kwargs: Any) -> str:
        """Use V2's atomic action when the client supports it; fall back to start+complete."""
        if hasattr(self.client, "record_atomic_action"):
            return self.client.record_atomic_action(**kwargs)
        result = kwargs.pop("result", None)
        local = self.client.start_task(**kwargs)
        self.client.complete_task(local, result or {})
        return local

    def _on_transaction_completed(self, e: Mapping[str, Any]) -> None:
        # Transactions are atomic ACTIONs nested under the current hourly plan.
        desc = f"sold {e['sku']} to {e['customer']} for ${e['price_cents'] / 100:.2f}"
        self._atomic_or_pair(
            parent_local_uuid = self.current_hourly_local_uuid,
            task_type         = "ACTION",
            description       = desc,
            event_spo         = (VENDY, "sold", e["sku"]),
            payload           = {
                "customer":    e["customer"],
                "sku":         e["sku"],
                "price_cents": e["price_cents"],
                "timestamp":   e["timestamp"],
            },
            result            = {"timestamp": e["timestamp"]},
        )

    def _on_business_action_committed(self, e: Mapping[str, Any]) -> None:
        # Business decisions (price changes, restocks, ...) are atomic ACTIONs
        # nested under the current hourly plan.
        self._atomic_or_pair(
            parent_local_uuid = self.current_hourly_local_uuid,
            task_type         = "ACTION",
            description       = e["description"],
            event_spo         = tuple(e.get("event_spo") or (VENDY, "did", e.get("action") or "action")),
            payload           = e.get("payload") or {},
            result            = {"timestamp": e.get("timestamp")},
        )
