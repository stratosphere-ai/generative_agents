"""Tests for V2 surface on the mock client + adapter atomic path + V1 fallback."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "reverie" / "backend_server"))


@pytest.fixture()
def fresh_bus():
    from vending.events import bus
    bus.reset()
    yield bus
    bus.reset()


# ---- MockTaskRegistryClient V2 surface -----------------------------------


def test_mock_record_atomic_action_assigns_id_and_marks_completed():
    from blockchain.mock import MockTaskRegistryClient

    client = MockTaskRegistryClient()
    local_uuid = client.record_atomic_action(
        parent_local_uuid = None,
        task_type         = "ACTION",
        description       = "sold Coke to Marco for $1.50",
        event_spo         = ("Vendy Unit-001", "sold", "Coke"),
        result            = {"timestamp": "t"},
    )
    tasks = client.all_tasks()
    assert len(tasks) == 1
    assert tasks[0].status == "completed"
    assert tasks[0].task_type == "ACTION"
    assert tasks[0].on_chain_id == 1
    assert ("atomic", local_uuid, 1, 0, "ACTION", "sold Coke to Marco for $1.50") in client.calls


def test_mock_atomic_links_to_parent():
    from blockchain.mock import MockTaskRegistryClient
    client = MockTaskRegistryClient()
    parent = client.start_task(parent_local_uuid=None, task_type="HOURLY",
                               description="lunch", event_spo=("V", "plans", "lunch"))
    child  = client.record_atomic_action(parent_local_uuid=parent, task_type="ACTION",
                                         description="atomic", event_spo=("V", "did", "x"))
    parent_id = client._local_to_id[parent]
    child_id  = client._local_to_id[child]
    assert client._tasks[child_id].parent_id == parent_id


def test_mock_start_task_batch_assigns_monotonic_ids():
    from blockchain.mock import MockTaskRegistryClient
    client = MockTaskRegistryClient()
    items = [
        {"parent_local_uuid": None, "task_type": "ACTION",
         "description": f"d{i}", "event_spo": ("V", "did", f"d{i}")}
        for i in range(3)
    ]
    uuids = client.start_task_batch(items)
    assert len(uuids) == 3
    ids = [client._local_to_id[u] for u in uuids]
    assert ids == [1, 2, 3]
    assert ("start_batch", 3) in client.calls


def test_mock_complete_task_batch_flips_status():
    from blockchain.mock import MockTaskRegistryClient
    client = MockTaskRegistryClient()
    starts = client.start_task_batch([
        {"parent_local_uuid": None, "task_type": "ACTION", "description": "a",
         "event_spo": ("V", "did", "a")},
        {"parent_local_uuid": None, "task_type": "ACTION", "description": "b",
         "event_spo": ("V", "did", "b")},
    ])
    client.complete_task_batch([{"local_uuid": starts[0], "result": {"r": 1}},
                                 {"local_uuid": starts[1], "result": {"r": 2}}])
    for u in starts:
        oid = client._local_to_id[u]
        assert client._tasks[oid].status == "completed"
    assert ("complete_batch", 2) in client.calls


# ---- Adapter prefers atomic when available ------------------------------


def test_adapter_uses_atomic_for_transaction_completed(fresh_bus):
    from vending.registry_adapter import TaskRegistryAdapter, VENDY
    from blockchain.mock import MockTaskRegistryClient

    client  = MockTaskRegistryClient()
    adapter = TaskRegistryAdapter(client)
    adapter.attach()

    fresh_bus.publish({"kind": "hourly_start", "persona": VENDY, "hour": 12,
                       "date": "2026-04-28", "description": "lunch peak"})
    fresh_bus.publish({
        "kind":       "transaction_completed",
        "persona":    VENDY,
        "sku":        "Coke",
        "price_cents": 150,
        "customer":   "Marco",
        "timestamp":  "2026-04-28T12:34:56",
        "parent_local_uuid": None,
    })

    # The HOURLY parent uses start_task (regular pair); the sale uses atomic.
    kinds = [c[0] for c in client.calls]
    assert kinds.count("atomic") == 1
    assert kinds.count("start") == 1     # only the HOURLY parent

    sale = [t for t in client.all_tasks() if t.task_type == "ACTION"][0]
    assert sale.status == "completed"
    assert sale.event_spo == (VENDY, "sold", "Coke")


def test_adapter_falls_back_to_start_complete_pair_when_no_atomic():
    """If the client lacks record_atomic_action, the adapter still works."""
    from vending.events import bus
    bus.reset()
    from vending.registry_adapter import TaskRegistryAdapter, VENDY

    class _LegacyClient:
        def __init__(self):
            self.calls = []
            self._next = 1
            self._local_to_id = {}
        def start_task(self, *, parent_local_uuid, task_type, description, event_spo, payload=None):
            import uuid
            u = uuid.uuid4().hex
            self._local_to_id[u] = self._next
            self._next += 1
            self.calls.append(("start", u, task_type, description))
            return u
        def complete_task(self, local_uuid, result=None):
            self.calls.append(("complete", local_uuid))

    client = _LegacyClient()
    TaskRegistryAdapter(client).attach()
    bus.publish({"kind": "hourly_start", "persona": VENDY, "hour": 12,
                 "date": "2026-04-28", "description": "lunch"})
    bus.publish({"kind": "transaction_completed", "persona": VENDY,
                 "sku": "Coke", "price_cents": 150, "customer": "Marco",
                 "timestamp": "t", "parent_local_uuid": None})
    kinds = [c[0] for c in client.calls]
    # 1 hourly start + 1 sale start + 1 sale complete (no atomic).
    assert kinds == ["start", "start", "complete"]
    bus.reset()


# ---- TaskRegistryV2 contract ABI shape sanity-check ----------------------


def test_contract_v2_sol_declares_expected_functions():
    """Static check: V2 contract source must contain the four new entry points."""
    sol_path = REPO_ROOT / "contracts" / "src" / "TaskRegistryV2.sol"
    src = sol_path.read_text(encoding="utf-8")
    for needle in (
        "function startTaskBatch",
        "function completeTaskBatch",
        "function recordAtomicAction(",
        "function recordAtomicActionBatch",
        "event AtomicActionRecorded(",
        "error EmptyBatch",
    ):
        assert needle in src, f"missing in V2 contract: {needle}"
