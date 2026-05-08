"""Reconcile sim journal against on-chain TaskRegistry state.

Three modes:

  --mode summary           offline; just summarize the journal (no chain)
  --mode chain             query TaskRegistry via web3.py and diff
  --mode chain --output FILE  write a JSON diff report to FILE

Diff buckets reported:

  unsubmitted_intents      intent_start in journal, no tx_start receipt yet
  pending_completes        intent_complete journaled but tx_complete missing
  pending_fails            intent_fail journaled but tx_fail missing
  chain_only_starts        TaskStarted on chain but no journal intent_start
                           (chain ahead of journal — journal corruption?)
  status_mismatch          journal says Completed but chain still Started, etc.

Exit code 0 on no diff, 1 on any drift found.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


def _load_journal(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _journal_view(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Index journal entries by local_uuid, derive intent vs receipt status."""
    by_local: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "intent_start": False, "tx_start": None,
        "intent_complete": False, "tx_complete": None,
        "intent_fail": False, "tx_fail": None,
        "description": None, "task_type": None, "on_chain_id": None,
        "errors": [],
    })
    for rec in records:
        kind = rec.get("kind")
        uuid = rec.get("local_uuid")
        if not uuid:
            continue
        slot = by_local[uuid]
        if kind == "intent_start":
            slot["intent_start"] = True
            slot["description"] = rec.get("description")
            slot["task_type"]   = rec.get("task_type")
        elif kind == "tx_start":
            slot["tx_start"]    = rec.get("tx_hash")
            slot["on_chain_id"] = rec.get("on_chain_id")
        elif kind == "intent_complete":
            slot["intent_complete"] = True
        elif kind == "tx_complete":
            slot["tx_complete"]  = rec.get("tx_hash")
            slot["on_chain_id"]  = rec.get("on_chain_id") or slot["on_chain_id"]
        elif kind == "intent_fail":
            slot["intent_fail"] = True
        elif kind == "tx_fail":
            slot["tx_fail"]      = rec.get("tx_hash")
        elif kind in ("dryrun_skipped", "dryrun_fallback", "pending_retry"):
            slot["errors"].append(kind)
    return dict(by_local)


def _summary_diff(view: dict[str, Any]) -> dict[str, Any]:
    """Offline-only summary: which intents lack receipts."""
    unsubmitted_intents: list[str] = []
    pending_completes:   list[str] = []
    pending_fails:       list[str] = []
    for uuid, slot in view.items():
        if slot["intent_start"] and not slot["tx_start"]:
            unsubmitted_intents.append(uuid)
        if slot["intent_complete"] and not slot["tx_complete"]:
            pending_completes.append(uuid)
        if slot["intent_fail"] and not slot["tx_fail"]:
            pending_fails.append(uuid)
    return {
        "total_local_uuids":   len(view),
        "unsubmitted_intents": sorted(unsubmitted_intents),
        "pending_completes":   sorted(pending_completes),
        "pending_fails":       sorted(pending_fails),
    }


# ---------------------------------------------------------------------------
# On-chain side (web3) — lazy-imported so summary mode has no extra deps.
# ---------------------------------------------------------------------------


def _query_chain_events(rpc_url: str, contract_address: str, abi_path: Path) -> dict[int, dict[str, Any]]:
    from web3 import Web3                                     # type: ignore[import-not-found]

    w3 = Web3(Web3.HTTPProvider(rpc_url))
    if not w3.is_connected():
        raise RuntimeError(f"cannot connect to {rpc_url}")
    abi = json.loads(abi_path.read_text(encoding="utf-8"))
    contract = w3.eth.contract(
        address=Web3.to_checksum_address(contract_address),
        abi=abi,
    )

    on_chain: dict[int, dict[str, Any]] = {}
    started = contract.events.TaskStarted.create_filter(fromBlock=0).get_all_entries()
    for e in started:
        on_chain[int(e["args"]["id"])] = {
            "id":       int(e["args"]["id"]),
            "status":   "Started",
            "agent":    e["args"]["agent"],
            "tx":       e["transactionHash"].hex(),
            "block":    e["blockNumber"],
        }
    for e in contract.events.TaskCompleted.create_filter(fromBlock=0).get_all_entries():
        tid = int(e["args"]["id"])
        if tid in on_chain:
            on_chain[tid]["status"] = "Completed"
            on_chain[tid]["complete_tx"] = e["transactionHash"].hex()
    for e in contract.events.TaskFailed.create_filter(fromBlock=0).get_all_entries():
        tid = int(e["args"]["id"])
        if tid in on_chain:
            on_chain[tid]["status"] = "Failed"
            on_chain[tid]["fail_tx"] = e["transactionHash"].hex()
    return on_chain


def _full_diff(view: dict[str, Any], on_chain: dict[int, dict[str, Any]]) -> dict[str, Any]:
    """Three-way diff: journal intents, journal receipts, chain state."""
    summary = _summary_diff(view)

    journal_ids = {slot["on_chain_id"]: uuid for uuid, slot in view.items() if slot.get("on_chain_id")}
    chain_only_starts: list[int] = []
    status_mismatch:    list[dict[str, Any]] = []

    for cid, ev in on_chain.items():
        if cid not in journal_ids:
            chain_only_starts.append(cid)
            continue
        uuid = journal_ids[cid]
        slot = view[uuid]
        # Journal "knows" completion if intent_complete fired.
        journal_status = (
            "Completed" if slot["intent_complete"] else
            "Failed"    if slot["intent_fail"]     else
            "Started"
        )
        if journal_status != ev["status"]:
            status_mismatch.append({
                "on_chain_id":     cid,
                "local_uuid":      uuid,
                "journal_status":  journal_status,
                "chain_status":    ev["status"],
            })

    out = dict(summary)
    out["chain_only_starts"] = sorted(chain_only_starts)
    out["status_mismatch"]   = status_mismatch
    out["chain_total_tasks"] = len(on_chain)
    return out


def _has_drift(report: dict[str, Any]) -> bool:
    keys = ("unsubmitted_intents", "pending_completes", "pending_fails",
            "chain_only_starts", "status_mismatch")
    return any(report.get(k) for k in keys)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _print_human(report: dict[str, Any]) -> None:
    def line(label: str, val: Any) -> None:
        if isinstance(val, list):
            print(f"  {label:24s} {len(val):>4d}  {val[:5]}{'…' if len(val) > 5 else ''}")
        else:
            print(f"  {label:24s} {val}")

    print("=== reconcile report ===")
    line("total local_uuids",   report.get("total_local_uuids", 0))
    line("unsubmitted_intents", report.get("unsubmitted_intents", []))
    line("pending_completes",   report.get("pending_completes", []))
    line("pending_fails",       report.get("pending_fails", []))
    if "chain_total_tasks" in report:
        line("chain_total_tasks",   report["chain_total_tasks"])
        line("chain_only_starts",   report.get("chain_only_starts", []))
        line("status_mismatch",     report.get("status_mismatch", []))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sim", required=True, help="sim_code under environment/frontend_server/storage/")
    parser.add_argument("--mode", choices=("summary", "chain"), default="summary")
    parser.add_argument("--rpc-url",          default=os.environ.get("RPC_URL", "http://127.0.0.1:8545"))
    parser.add_argument("--contract-address", default=os.environ.get("CONTRACT_ADDRESS", ""))
    parser.add_argument("--abi-path",         default=os.environ.get(
        "TASK_REGISTRY_ABI",
        str(Path(__file__).resolve().parent.parent /
            "reverie" / "backend_server" / "blockchain" / "abi" / "TaskRegistry.json"),
    ))
    parser.add_argument("--storage",          default="environment/frontend_server/storage")
    parser.add_argument("--output",           help="write JSON report to this path (default: stdout)")
    args = parser.parse_args()

    sim_dir = Path(args.storage) / args.sim
    journal_path = sim_dir / "blockchain_journal.jsonl"
    if not sim_dir.is_dir():
        print(f"error: sim directory not found: {sim_dir}", file=sys.stderr)
        return 2

    records = _load_journal(journal_path)
    view = _journal_view(records)

    if args.mode == "summary":
        report = _summary_diff(view)
    else:
        if not args.contract_address:
            print("error: --contract-address (or env CONTRACT_ADDRESS) required for --mode chain", file=sys.stderr)
            return 2
        on_chain = _query_chain_events(args.rpc_url, args.contract_address, Path(args.abi_path))
        report = _full_diff(view, on_chain)

    if args.output:
        Path(args.output).write_text(json.dumps(report, indent=2, sort_keys=True))
        print(f"[reconcile] report written to {args.output}")
    _print_human(report)
    return 1 if _has_drift(report) else 0


if __name__ == "__main__":
    sys.exit(main())
