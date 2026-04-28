"""In-memory MockTaskRegistryClient for tests and BLOCKCHAIN_MODE=mock runs.

Same public surface as TaskRegistryClient; assigns monotonically increasing
ids without any web3 / RPC dependency. Records every call in `.calls` for
test assertions.
"""

from __future__ import annotations

import threading
import uuid as _uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional


@dataclass
class _MockTask:
    on_chain_id: int
    parent_id: int
    task_type: str
    description: str
    event_spo: tuple[str, str, str]
    status: str = "started"
    result: Mapping[str, Any] = field(default_factory=dict)
    reason: Optional[str] = None


class MockTaskRegistryClient:
    def __init__(self, *, payload_dir: Path | None = None) -> None:
        self._lock = threading.Lock()
        self._next_id = 1
        self._local_to_id: dict[str, int] = {}
        self._tasks: dict[int, _MockTask] = {}
        self.calls: list[tuple] = []
        self._payload_dir = payload_dir
        if payload_dir is not None:
            payload_dir.mkdir(parents=True, exist_ok=True)

    def start_task(
        self,
        *,
        parent_local_uuid: Optional[str],
        task_type: str,
        description: str,
        event_spo: tuple[str, str, str],
        payload: Mapping[str, Any] | None = None,
    ) -> str:
        with self._lock:
            local_uuid = _uuid.uuid4().hex
            parent_id = self._local_to_id.get(parent_local_uuid, 0) if parent_local_uuid else 0
            on_chain_id = self._next_id
            self._next_id += 1
            self._local_to_id[local_uuid] = on_chain_id
            self._tasks[on_chain_id] = _MockTask(
                on_chain_id = on_chain_id,
                parent_id   = parent_id,
                task_type   = task_type,
                description = description,
                event_spo   = event_spo,
            )
        self.calls.append(("start", local_uuid, on_chain_id, parent_id, task_type, description))
        return local_uuid

    def complete_task(self, local_uuid: str, result: Mapping[str, Any] | None = None) -> None:
        with self._lock:
            on_chain_id = self._local_to_id.get(local_uuid)
            if on_chain_id is None:
                raise KeyError(f"unknown local_uuid {local_uuid!r}")
            t = self._tasks[on_chain_id]
            t.status = "completed"
            t.result = dict(result or {})
        self.calls.append(("complete", local_uuid, on_chain_id))

    def fail_task(self, local_uuid: str, reason: str) -> None:
        with self._lock:
            on_chain_id = self._local_to_id.get(local_uuid)
            if on_chain_id is None:
                raise KeyError(f"unknown local_uuid {local_uuid!r}")
            t = self._tasks[on_chain_id]
            t.status = "failed"
            t.reason = reason
        self.calls.append(("fail", local_uuid, on_chain_id, reason))

    def shutdown(self, *_: Any, **__: Any) -> None:
        pass

    # --- test helpers -----------------------------------------------------

    def task(self, on_chain_id: int) -> _MockTask:
        return self._tasks[on_chain_id]

    def all_tasks(self) -> list[_MockTask]:
        return [self._tasks[i] for i in sorted(self._tasks)]
