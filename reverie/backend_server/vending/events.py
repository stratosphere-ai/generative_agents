"""Tiny in-process pub/sub bus for vending lifecycle events.

Plan-loop hooks publish events here without knowing about TaskRegistry,
storage, or any other downstream concern. Subscribers are added at server
startup time.

Subscribers MUST be fast and non-blocking; long work belongs on a worker
thread. The bus catches and logs subscriber exceptions so a buggy listener
cannot break the sim tick.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Mapping


log = logging.getLogger(__name__)

Event = Mapping[str, Any]
Listener = Callable[[Event], None]


class _Bus:
    def __init__(self) -> None:
        self._listeners: list[Listener] = []

    def subscribe(self, listener: Listener) -> None:
        self._listeners.append(listener)

    def unsubscribe(self, listener: Listener) -> None:
        try:
            self._listeners.remove(listener)
        except ValueError:
            pass

    def publish(self, event: Event) -> None:
        for listener in list(self._listeners):
            try:
                listener(event)
            except Exception:                                 # noqa: BLE001
                log.exception("vending bus listener raised; continuing")

    def reset(self) -> None:
        """Clear all subscribers (test helper)."""
        self._listeners.clear()


bus = _Bus()
