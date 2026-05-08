"""Background tx submission queue with bounded retries + optional batch window.

Items are opaque tuples; the worker callable knows how to interpret them.
On exception, single items are retried with exponential backoff up to
MAX_RETRIES; after that the journal records a `pending_retry` line and
the item is dropped from the in-memory queue (it can be replayed offline
from the journal).

Batch mode: when the constructor is given `batch_window_sec > 0` AND a
`batch_worker` callable, the worker thread waits up to `batch_window_sec`
after the first item arrives, drains everything else in the queue (up to
`max_batch_size`), and calls `batch_worker(list_of_items)` once. Failed
batches re-enqueue every member as an individual single-item retry so the
non-batched path can take over (caps the blast radius of a flaky batch).
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
        worker:           Callable[[tuple], None],
        journal:          Journal,
        max_size:         int = 1024,
        batch_window_sec: float = 0.0,
        batch_worker:     Callable[[list[tuple]], None] | None = None,
        max_batch_size:   int = 50,
    ) -> None:
        self._worker          = worker
        self._batch_worker    = batch_worker
        self._journal         = journal
        self._batch_window    = max(0.0, float(batch_window_sec))
        self._max_batch_size  = max(1, int(max_batch_size))
        self._q: "queue.Queue[tuple[Any, int]]" = queue.Queue(maxsize=max_size)
        self._stop_evt = threading.Event()
        self._thread   = threading.Thread(target=self._run, name="tx-queue", daemon=True)
        self._thread.start()

    @property
    def batching_enabled(self) -> bool:
        return self._batch_window > 0.0 and self._batch_worker is not None

    def enqueue(self, item: tuple) -> None:
        self._q.put((item, 0))

    def shutdown(self, *, timeout: float = 10.0) -> None:
        self._stop_evt.set()
        self._q.put((("__stop__",), 0))
        self._thread.join(timeout=timeout)

    # ------------------------------------------------------------------

    def _run(self) -> None:
        while not self._stop_evt.is_set():
            item, attempt = self._q.get()
            if item == ("__stop__",):
                return

            if self.batching_enabled:
                # Anchor the window on the first item; drain the rest with
                # remaining time. Items inside one window all go to the batch
                # worker as a single list. We attach the per-item attempt
                # counters in parallel so retries keep their fairness.
                items   = [item]
                attempts = [attempt]
                deadline = time.monotonic() + self._batch_window
                while len(items) < self._max_batch_size:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    try:
                        nxt, nxt_attempt = self._q.get(timeout=remaining)
                    except queue.Empty:
                        break
                    if nxt == ("__stop__",):
                        # Process what we have, then exit.
                        self._dispatch_batch(items, attempts)
                        return
                    items.append(nxt)
                    attempts.append(nxt_attempt)
                self._dispatch_batch(items, attempts)
            else:
                self._dispatch_single(item, attempt)

    # ---- single-item path (legacy) -----------------------------------

    def _dispatch_single(self, item: tuple, attempt: int) -> None:
        try:
            self._worker(item)
        except Exception as exc:                                  # noqa: BLE001
            self._handle_retry_one(item, attempt, exc)

    def _handle_retry_one(self, item: tuple, attempt: int, exc: BaseException) -> None:
        if attempt >= MAX_RETRIES:
            log.error("tx-queue dropping item after %d retries: %r (%s)", attempt, item, exc)
            self._journal.append("pending_retry", item=list(item), attempt=attempt, error=str(exc))
            return
        backoff = INITIAL_BACKOFF_S * (BACKOFF_MULTIPLIER ** attempt)
        log.warning("tx-queue retrying %r in %.1fs (attempt %d): %s", item, backoff, attempt + 1, exc)
        time.sleep(backoff)
        self._q.put((item, attempt + 1))

    # ---- batched path ------------------------------------------------

    def _dispatch_batch(self, items: list[tuple], attempts: list[int]) -> None:
        if not items:
            return
        try:
            self._batch_worker(items)                              # type: ignore[misc]
            return
        except Exception as exc:                                   # noqa: BLE001
            if len(items) == 1:
                self._handle_retry_one(items[0], attempts[0], exc)
                return
            log.warning("tx-queue batch failed (n=%d): %s; isolating each item via single worker",
                        len(items), exc)
            self._journal.append("batch_failed", count=len(items), error=str(exc))
            # Skip the batch worker on retry: dispatch each item via the
            # per-item worker so a poison item gets isolated on this pass.
            for it, a in zip(items, attempts):
                try:
                    self._worker(it)
                except Exception as exc2:                          # noqa: BLE001
                    self._handle_retry_one(it, a, exc2)
