"""Append-only JSONL journal of every TaskRegistry interaction.

The journal is the single source of truth for sim/chain reconciliation:
    - sim writes a line BEFORE submitting a tx (intent),
    - sim writes a line AFTER receiving a receipt or hitting a terminal failure.
On restart, replay tools read the journal and reconcile against on-chain state.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Mapping


class Journal:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)

    @property
    def path(self) -> Path:
        return self._path

    def append(self, kind: str, **fields: Any) -> None:
        record = {"ts": time.time(), "kind": kind, **fields}
        line = json.dumps(record, sort_keys=True, default=str)
        with self._lock, self._path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def read_all(self) -> list[Mapping[str, Any]]:
        with self._path.open("r", encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]
