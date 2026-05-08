"""Tests for TxQueue's batch-window mode + retry semantics on batch failure."""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "reverie" / "backend_server"))


def _make_journal(tmp_path):
    from blockchain.journal import Journal
    return Journal(tmp_path / "journal.jsonl")


def test_single_item_path_unchanged_when_window_zero(tmp_path):
    from blockchain.queue import TxQueue
    seen: list[tuple] = []
    q = TxQueue(worker=lambda it: seen.append(it),
                journal=_make_journal(tmp_path))
    try:
        q.enqueue(("start", "a"))
        q.enqueue(("start", "b"))
        # Wait for worker to drain.
        for _ in range(100):
            if len(seen) == 2:
                break
            time.sleep(0.01)
    finally:
        q.shutdown(timeout=2.0)
    assert seen == [("start", "a"), ("start", "b")]


def test_batch_window_collects_items_into_one_call(tmp_path):
    from blockchain.queue import TxQueue
    batches: list[list[tuple]] = []

    def batch_worker(items):
        batches.append(list(items))

    q = TxQueue(worker=lambda it: pytest.fail("single-worker should not run"),
                batch_worker=batch_worker,
                batch_window_sec=0.1,
                journal=_make_journal(tmp_path))
    try:
        q.enqueue(("start", "a"))
        q.enqueue(("start", "b"))
        q.enqueue(("start", "c"))
        # Wait long enough for the window to close.
        for _ in range(100):
            if batches:
                break
            time.sleep(0.01)
    finally:
        q.shutdown(timeout=2.0)
    assert len(batches) == 1
    assert [it[1] for it in batches[0]] == ["a", "b", "c"]


def test_items_arriving_after_window_go_in_next_batch(tmp_path):
    from blockchain.queue import TxQueue
    batches: list[list[tuple]] = []

    q = TxQueue(worker=lambda it: None,    # not used in batching mode
                batch_worker=lambda items: batches.append(list(items)),
                batch_window_sec=0.05,
                journal=_make_journal(tmp_path))
    try:
        q.enqueue(("start", "a"))
        for _ in range(50):
            if batches:
                break
            time.sleep(0.01)
        q.enqueue(("start", "b"))
        for _ in range(50):
            if len(batches) == 2:
                break
            time.sleep(0.01)
    finally:
        q.shutdown(timeout=2.0)
    assert len(batches) == 2
    assert batches[0][0][1] == "a"
    assert batches[1][0][1] == "b"


def test_single_item_in_window_still_calls_batch_worker(tmp_path):
    """In batching mode, single items go to batch_worker (which can choose
    to fall back to single-item path internally). This keeps the worker
    contract uniform."""
    from blockchain.queue import TxQueue
    single_calls: list[tuple] = []
    batch_calls:  list[list[tuple]] = []

    q = TxQueue(worker=lambda it: single_calls.append(it),
                batch_worker=lambda items: batch_calls.append(list(items)),
                batch_window_sec=0.05,
                journal=_make_journal(tmp_path))
    try:
        q.enqueue(("start", "lonely"))
        for _ in range(50):
            if batch_calls:
                break
            time.sleep(0.01)
    finally:
        q.shutdown(timeout=2.0)
    assert batch_calls == [[("start", "lonely")]]
    assert single_calls == []


def test_batch_failure_reenqueues_items_individually(tmp_path):
    from blockchain.queue import TxQueue
    single_calls: list[tuple] = []
    batch_calls:  list[list[tuple]] = []

    def batch_worker(items):
        batch_calls.append(list(items))
        raise RuntimeError("simulated batch failure")

    q = TxQueue(worker=lambda it: single_calls.append(it),
                batch_worker=batch_worker,
                batch_window_sec=0.05,
                journal=_make_journal(tmp_path))
    try:
        q.enqueue(("start", "a"))
        q.enqueue(("start", "b"))
        # Wait for batch to fail and re-enqueued items to be processed individually.
        deadline = time.time() + 3.0
        while time.time() < deadline and len(single_calls) < 2:
            time.sleep(0.02)
    finally:
        q.shutdown(timeout=3.0)
    assert len(batch_calls) == 1
    # After failure, both items came through the single-item path.
    assert sorted(it[1] for it in single_calls) == ["a", "b"]


def test_max_batch_size_caps_collection(tmp_path):
    from blockchain.queue import TxQueue
    batches: list[list[tuple]] = []

    q = TxQueue(worker=lambda it: pytest.fail("single-worker should not run"),
                batch_worker=lambda items: batches.append(list(items)),
                batch_window_sec=0.5,
                max_batch_size=3,
                journal=_make_journal(tmp_path))
    try:
        for i in range(7):
            q.enqueue(("start", str(i)))
        deadline = time.time() + 3.0
        while time.time() < deadline and sum(len(b) for b in batches) < 7:
            time.sleep(0.02)
    finally:
        q.shutdown(timeout=3.0)
    # Each batch is at most 3 items.
    assert all(len(b) <= 3 for b in batches)
    assert sum(len(b) for b in batches) == 7


# ---- Client integration: kind grouping inside _submit_many --------------


def test_client_submit_many_groups_by_kind_via_mock_dryrun(tmp_path, monkeypatch):
    from blockchain.client import TaskRegistryClient
    from blockchain.config import BlockchainConfig

    cfg = BlockchainConfig(
        mode="dryrun",
        rpc_url="http://localhost",
        chain_id=31337,
        private_key="",
        contract_address="",
        abi_path=tmp_path / "abi.json",
        journal_path=tmp_path / "journal.jsonl",
        payload_dir=tmp_path / "payloads",
        batch_window_sec=0.1,
    )
    client = TaskRegistryClient(cfg)
    try:
        client.start_task(parent_local_uuid=None, task_type="ACTION",
                          description="a", event_spo=("V", "did", "a"))
        client.start_task(parent_local_uuid=None, task_type="ACTION",
                          description="b", event_spo=("V", "did", "b"))
        client.fail_task(client.start_task(parent_local_uuid=None, task_type="ACTION",
                                           description="c", event_spo=("V", "did", "c")),
                         reason="rip")
        # Wait for window to close.
        time.sleep(0.4)
        # Read journal: in dryrun the batch worker writes a `dryrun_skipped_batch` line
        records = client.journal.read_all()
    finally:
        client.shutdown(timeout=2.0)
    kinds = [r["kind"] for r in records]
    # We should see at least one dryrun_skipped_batch entry covering multiple items.
    assert any(k == "dryrun_skipped_batch" for k in kinds)
