"""Tests for scripts/reconcile.py — offline summary + diff helpers."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))


@pytest.fixture()
def reconcile():
    if "reconcile" in sys.modules:
        del sys.modules["reconcile"]
    import reconcile as r
    return r


def _journal_records():
    return [
        # u1: intent_start + tx_start + intent_complete + tx_complete (clean)
        {"kind": "intent_start",    "local_uuid": "u1", "description": "lunch peak", "task_type": "HOURLY"},
        {"kind": "tx_start",        "local_uuid": "u1", "on_chain_id": 1, "tx_hash": "0xaaa"},
        {"kind": "intent_complete", "local_uuid": "u1"},
        {"kind": "tx_complete",     "local_uuid": "u1", "on_chain_id": 1, "tx_hash": "0xbbb"},

        # u2: intent_start only (unsubmitted)
        {"kind": "intent_start",    "local_uuid": "u2", "description": "stuck", "task_type": "ACTION"},

        # u3: intent_start + tx_start + intent_complete (no tx_complete yet)
        {"kind": "intent_start",    "local_uuid": "u3", "description": "pending", "task_type": "ACTION"},
        {"kind": "tx_start",        "local_uuid": "u3", "on_chain_id": 2, "tx_hash": "0xccc"},
        {"kind": "intent_complete", "local_uuid": "u3"},

        # u4: intent_start + tx_start + intent_fail (no tx_fail yet)
        {"kind": "intent_start",    "local_uuid": "u4", "description": "going to fail", "task_type": "ACTION"},
        {"kind": "tx_start",        "local_uuid": "u4", "on_chain_id": 3, "tx_hash": "0xddd"},
        {"kind": "intent_fail",     "local_uuid": "u4"},
    ]


def test_journal_view_indexes_by_local_uuid(reconcile):
    view = reconcile._journal_view(_journal_records())
    assert set(view.keys()) == {"u1", "u2", "u3", "u4"}
    assert view["u1"]["intent_complete"] is True
    assert view["u1"]["tx_complete"] == "0xbbb"
    assert view["u2"]["tx_start"]    is None
    assert view["u3"]["tx_complete"] is None
    assert view["u4"]["intent_fail"] is True
    assert view["u4"]["tx_fail"]     is None


def test_summary_diff_separates_buckets(reconcile):
    view = reconcile._journal_view(_journal_records())
    report = reconcile._summary_diff(view)
    assert report["total_local_uuids"]   == 4
    assert report["unsubmitted_intents"] == ["u2"]
    assert report["pending_completes"]   == ["u3"]
    assert report["pending_fails"]       == ["u4"]


def test_full_diff_detects_chain_only_starts(reconcile):
    view = reconcile._journal_view(_journal_records())
    on_chain = {
        1: {"id": 1, "status": "Completed", "agent": "0xa11ce", "tx": "0xaaa", "block": 10},
        2: {"id": 2, "status": "Started",   "agent": "0xa11ce", "tx": "0xccc", "block": 11},
        3: {"id": 3, "status": "Failed",    "agent": "0xa11ce", "tx": "0xddd", "block": 12},
        # Chain has an extra task (4) the journal never recorded.
        4: {"id": 4, "status": "Started",   "agent": "0xa11ce", "tx": "0xeee", "block": 13},
    }
    report = reconcile._full_diff(view, on_chain)
    assert report["chain_total_tasks"]  == 4
    assert report["chain_only_starts"]  == [4]
    # u3 journal-status is "Completed" (intent_complete fired) but chain still "Started".
    assert any(m["on_chain_id"] == 2 and m["chain_status"] == "Started"
               and m["journal_status"] == "Completed" for m in report["status_mismatch"])
    # u4 journal-status "Failed" matches chain "Failed" — no mismatch.
    assert not any(m["on_chain_id"] == 3 for m in report["status_mismatch"])


def test_has_drift_returns_true_when_buckets_nonempty(reconcile):
    view = reconcile._journal_view(_journal_records())
    report = reconcile._summary_diff(view)
    assert reconcile._has_drift(report) is True


def test_clean_journal_has_no_drift(reconcile):
    records = [
        {"kind": "intent_start",    "local_uuid": "u1", "description": "x", "task_type": "ACTION"},
        {"kind": "tx_start",        "local_uuid": "u1", "on_chain_id": 1, "tx_hash": "0xa"},
        {"kind": "intent_complete", "local_uuid": "u1"},
        {"kind": "tx_complete",     "local_uuid": "u1", "on_chain_id": 1, "tx_hash": "0xb"},
    ]
    view = reconcile._journal_view(records)
    report = reconcile._summary_diff(view)
    assert reconcile._has_drift(report) is False


def test_load_journal_skips_blank_and_invalid_lines(tmp_path, reconcile):
    p = tmp_path / "j.jsonl"
    p.write_text(
        '{"kind":"intent_start","local_uuid":"u1"}\n'
        '\n'
        'this is not json\n'
        '{"kind":"tx_start","local_uuid":"u1","on_chain_id":7,"tx_hash":"0x7"}\n'
    )
    recs = reconcile._load_journal(p)
    assert len(recs) == 2
    assert recs[1]["on_chain_id"] == 7
