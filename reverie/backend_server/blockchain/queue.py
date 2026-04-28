"""Background tx submission queue with bounded retries.

Items are opaque tuples; the worker callable knows how to interpret them.
On exception, items are retried with exponential backoff up to MAX_RETRIES;
after that the journal records a `pending_retry` line and the item is dropped
from the in-memory queue (it can be replayed offline from the journal).
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Any, Callable

from .journal import Journal


log = logging.getLogger(__name__)

MAX_RETRIES        = 3
INITIAL_BACKOFF_S  = 1.0
BACKOFF_MULTIPLIER = 2.0


class TxQueue:
    def __init__(
        self,
        *,
        worker: Callable[[tuple], None],
        journal: Journal,
        max_size: int = 1024,
    ) -> None:
        self._worker  = worker
        self._journal = journal
        self._q: "queue.Queue[tuple[Any, int]]" = queue.Queue(maxsize=max_size)
        self._stop_evt = threading.Event()
        self._thread   = threading.Thread(target=self._run, name="tx-queue", daemon=True)
        self._thread.start()

    def enqueue(self, item: tuple) -> None:
        self._q.put((item, 0))

    def shutdown(self, *, timeout: float = 10.0) -> None:
        self._stop_evt.set()
        self._q.put((("__stop__",), 0))
        self._thread.join(timeout=timeout)

    def _run(self) -> None:
        while not self._stop_evt.is_set():
            item, attempt = self._q.get()
            if item == ("__stop__",):
                return
            try:
                self._worker(item)
            except Exception as exc:                                 # noqa: BLE001
                if attempt >= MAX_RETRIES:
                    log.error("tx-queue dropping item after %d retries: %r (%s)", attempt, item, exc)
                    self._journal.append(
                        "pending_retry",
                        item    = list(item),
                        attempt = attempt,
                        error   = str(exc),
                    )
                    continue
                backoff = INITIAL_BACKOFF_S * (BACKOFF_MULTIPLIER ** attempt)
                log.warning("tx-queue retrying %r in %.1fs (attempt %d): %s", item, backoff, attempt + 1, exc)
                time.sleep(backoff)
                self._q.put((item, attempt + 1))
