"""Smoke test: bus events flow through TaskRegistryAdapter to MockTaskRegistryClient."""

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


def test_hourly_then_action_creates_parent_child(fresh_bus, tmp_path):
    from vending.registry_adapter import TaskRegistryAdapter, VENDY
    from blockchain.mock import MockTaskRegistryClient

    client  = MockTaskRegistryClient(payload_dir=tmp_path / "payloads")
    adapter = TaskRegistryAdapter(client)
    adapter.attach()

    fresh_bus.publish({
        "kind":        "hourly_start",
        "persona":     VENDY,
        "hour":        12,
        "date":        "2026-04-28",
        "description": "operate vending machine through lunch peak",
    })
    fresh_bus.publish({
        "kind":        "action_start",
        "persona":     VENDY,
        "address":     "plaza_world:Central Plaza:Vending Corner:VendingMachine",
        "duration":    10,
        "description": "restock low SKUs",
        "event":       (VENDY, "restocks", "VendingMachine"),
        "action_key":  "restock-key",
    })
    fresh_bus.publish({
        "kind":         "action_end",
        "persona":      VENDY,
        "address":      "plaza_world:Central Plaza:Vending Corner:VendingMachine",
        "description":  "restock low SKUs",
        "completed_at": "2026-04-28T12:10:00",
        "action_key":   "restock-key",
    })
    fresh_bus.publish({
        "kind":         "hourly_end",
        "persona":      VENDY,
        "completed_at": "2026-04-28T13:00:00",
    })

    tasks = client.all_tasks()
    assert len(tasks) == 2

    parent, child = tasks
    assert parent.parent_id == 0
    assert parent.task_type == "HOURLY"
    assert parent.status    == "completed"
    assert child.parent_id  == parent.on_chain_id
    assert child.task_type  == "ACTION"
    assert child.status     == "completed"


def test_transaction_event_creates_atomic_action(fresh_bus, tmp_path):
    from vending.registry_adapter import TaskRegistryAdapter, VENDY
    from blockchain.mock import MockTaskRegistryClient

    client  = MockTaskRegistryClient(payload_dir=tmp_path / "payloads")
    adapter = TaskRegistryAdapter(client)
    adapter.attach()

    fresh_bus.publish({
        "kind":        "hourly_start",
        "persona":     VENDY,
        "hour":        12,
        "date":        "2026-04-28",
        "description": "lunch peak",
    })
    fresh_bus.publish({
        "kind":        "transaction_completed",
        "persona":     VENDY,
        "sku":         "Coke",
        "price_cents": 150,
        "customer":    "Marco Silva",
        "timestamp":   "2026-04-28T12:34:56",
        "parent_local_uuid": None,
    })

    tasks = client.all_tasks()
    assert len(tasks) == 2  # 1 HOURLY parent + 1 atomic ACTION
    sale = tasks[1]
    assert sale.task_type   == "ACTION"
    assert sale.parent_id   == tasks[0].on_chain_id
    assert sale.status      == "completed"
    assert sale.event_spo   == (VENDY, "sold", "Coke")


def test_non_vendy_events_ignored(fresh_bus, tmp_path):
    from vending.registry_adapter import TaskRegistryAdapter
    from blockchain.mock import MockTaskRegistryClient

    client = MockTaskRegistryClient(payload_dir=tmp_path / "payloads")
    TaskRegistryAdapter(client).attach()

    fresh_bus.publish({
        "kind":        "action_start",
        "persona":     "Lin Wei",
        "address":     "plaza_world:Central Plaza:Plaza Square",
        "duration":    5,
        "description": "walking through plaza",
        "event":       ("Lin Wei", "walks-to", "Plaza Square"),
        "action_key":  "lin-walk",
    })
    assert client.all_tasks() == []
