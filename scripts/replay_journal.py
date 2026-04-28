"""Replay an `intent_*` journal against an on-chain TaskRegistry.

Used after a sim crash or a long dryrun period to reconcile pending writes.
Currently a skeleton: it walks the journal, matches `intent_start` entries
without a corresponding `tx_start`, and re-issues them via TaskRegistryClient.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from blockchain.config import load_config
from blockchain.client import TaskRegistryClient


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sim", required=True, help="sim code to reconcile (under storage/)")
    args = parser.parse_args()

    sim_dir = Path("environment/frontend_server/storage") / args.sim
    journal_path = sim_dir / "blockchain_journal.jsonl"
    if not journal_path.exists():
        raise SystemExit(f"no journal at {journal_path}")

    cfg = load_config(sim_storage_dir=sim_dir)
    if cfg.mode != "live":
        raise SystemExit(f"replay requires BLOCKCHAIN_MODE=live; got {cfg.mode}")

    client = TaskRegistryClient(cfg)

    intents: dict[str, dict] = {}
    confirmed_starts: set[str] = set()
    for line in journal_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if rec["kind"] == "intent_start":
            intents[rec["local_uuid"]] = rec
        elif rec["kind"] == "tx_start":
            confirmed_starts.add(rec["local_uuid"])

    pending = [r for u, r in intents.items() if u not in confirmed_starts]
    print(f"[replay] {len(pending)} pending starts in {journal_path}")

    for rec in pending:
        print(f"[replay] retrying {rec['local_uuid']}: {rec['description']}")
        client.start_task(
            parent_local_uuid = rec.get("parent_local_uuid"),
            task_type         = rec["task_type"],
            description       = rec["description"],
            event_spo         = tuple(rec["event_spo"]),
        )

    client.shutdown(timeout=30.0)


if __name__ == "__main__":
    main()
