"""Web3 TaskRegistry client.

Public surface (used by vending.events subscribers):
    start_task(parent_local_uuid, task_type, description, event_spo, payload)
        -> local_uuid
    complete_task(local_uuid, result_payload) -> None
    fail_task(local_uuid, reason) -> None

All methods enqueue work onto a background TxQueue and return immediately;
sim ticks never block on chain I/O. Local UUIDs are issued synchronously and
mapped to on-chain ids once receipts arrive.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import uuid as _uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

from .config import BlockchainConfig
from .journal import Journal
from .queue import TxQueue


log = logging.getLogger(__name__)


def keccak256_text(text: str) -> bytes:
    # Keccak-256 of UTF-8 bytes. eth_utils.keccak handles this when present;
    # we lazy-import inside _ensure_web3 to avoid a hard dependency for tests.
    from eth_utils import keccak  # type: ignore[import-not-found]
    return keccak(text=text)


def keccak256_json(obj: Mapping[str, Any]) -> bytes:
    return keccak256_text(json.dumps(obj, sort_keys=True, default=str))


@dataclass
class _Pending:
    local_uuid: str
    parent_local_uuid: Optional[str]
    task_type: str
    description: str
    event_spo: tuple[str, str, str]
    payload_path: Path


class TaskRegistryClient:
    """Async, fire-and-forget TaskRegistry client backed by a worker thread.

    Lazy web3 init: connecting / loading ABI happens on the first submit, so
    unit tests can construct the client without an RPC available.
    """

    def __init__(self, config: BlockchainConfig) -> None:
        self.config   = config
        self.journal  = Journal(config.journal_path)
        self._queue   = TxQueue(
            worker           = self._submit,
            batch_worker     = self._submit_many,
            batch_window_sec = config.batch_window_sec,
            max_batch_size   = config.max_batch_size,
            journal          = self.journal,
        )
        self._pending: dict[str, _Pending] = {}
        self._local_to_onchain: dict[str, int] = {}
        self._lock    = threading.Lock()
        self._w3      = None
        self._account = None
        self._contract = None
        config.payload_dir.mkdir(parents=True, exist_ok=True)

    # ---------- public API ------------------------------------------------

    def start_task(
        self,
        *,
        parent_local_uuid: Optional[str],
        task_type: str,
        description: str,
        event_spo: tuple[str, str, str],
        payload: Mapping[str, Any] | None = None,
    ) -> str:
        local_uuid = _uuid.uuid4().hex
        payload_obj = dict(payload or {})
        payload_obj["description"] = description
        payload_obj["event_spo"]   = list(event_spo)
        payload_obj["task_type"]   = task_type

        payload_path = self.config.payload_dir / f"{local_uuid}.json"
        payload_path.write_text(json.dumps(payload_obj, sort_keys=True, default=str), encoding="utf-8")

        pending = _Pending(
            local_uuid       = local_uuid,
            parent_local_uuid = parent_local_uuid,
            task_type        = task_type,
            description      = description,
            event_spo        = event_spo,
            payload_path     = payload_path,
        )
        with self._lock:
            self._pending[local_uuid] = pending

        self.journal.append(
            "intent_start",
            local_uuid       = local_uuid,
            parent_local_uuid = parent_local_uuid,
            task_type        = task_type,
            description      = description,
            event_spo        = list(event_spo),
            payload_path     = str(payload_path),
        )
        self._queue.enqueue(("start", local_uuid))
        return local_uuid

    def complete_task(
        self,
        local_uuid: str,
        result: Mapping[str, Any] | None = None,
    ) -> None:
        result_obj = dict(result or {})
        result_path = self.config.payload_dir / f"{local_uuid}.result.json"
        result_path.write_text(json.dumps(result_obj, sort_keys=True, default=str), encoding="utf-8")

        self.journal.append(
            "intent_complete",
            local_uuid  = local_uuid,
            result_path = str(result_path),
        )
        self._queue.enqueue(("complete", local_uuid))

    def fail_task(self, local_uuid: str, reason: str) -> None:
        self.journal.append("intent_fail", local_uuid=local_uuid, reason=reason)
        self._queue.enqueue(("fail", local_uuid, reason))

    # ---- V2: atomic action -----------------------------------------------

    def record_atomic_action(
        self,
        *,
        parent_local_uuid: Optional[str],
        task_type: str,
        description: str,
        event_spo: tuple[str, str, str],
        result: Mapping[str, Any] | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> str:
        """Atomic start+complete in one tx (TaskRegistryV2.recordAtomicAction).

        Saves a full extra round-trip vs the start_task + complete_task pair.
        Use for sub-actions whose start and end are the same sim moment
        (sales, price changes, restocks, loans).
        """
        local_uuid = _uuid.uuid4().hex
        payload_obj = dict(payload or {})
        result_obj  = dict(result or {})
        payload_obj["description"] = description
        payload_obj["event_spo"]   = list(event_spo)
        payload_obj["task_type"]   = task_type
        payload_obj["atomic"]      = True

        payload_path = self.config.payload_dir / f"{local_uuid}.json"
        payload_path.write_text(json.dumps(payload_obj, sort_keys=True, default=str), encoding="utf-8")
        result_path  = self.config.payload_dir / f"{local_uuid}.result.json"
        result_path.write_text(json.dumps(result_obj, sort_keys=True, default=str), encoding="utf-8")

        pending = _Pending(
            local_uuid       = local_uuid,
            parent_local_uuid = parent_local_uuid,
            task_type        = task_type,
            description      = description,
            event_spo        = event_spo,
            payload_path     = payload_path,
        )
        with self._lock:
            self._pending[local_uuid] = pending

        self.journal.append(
            "intent_atomic",
            local_uuid       = local_uuid,
            parent_local_uuid = parent_local_uuid,
            task_type        = task_type,
            description      = description,
            event_spo        = list(event_spo),
            payload_path     = str(payload_path),
            result_path      = str(result_path),
        )
        self._queue.enqueue(("atomic", local_uuid))
        return local_uuid

    # ---- V2: batch helpers (queue thin wrappers) -------------------------

    def start_task_batch(self, items: list[Mapping[str, Any]]) -> list[str]:
        """Convenience wrapper: enqueue a `start_batch` instead of N separate starts.

        Items are kwarg dicts shaped like start_task. Returned local_uuids
        match input order. The submitted tx will be a single
        startTaskBatch(...) call on V2.
        """
        local_uuids: list[str] = []
        pendings: list[_Pending] = []
        records: list[dict[str, Any]] = []
        for it in items:
            local_uuid = _uuid.uuid4().hex
            payload_obj = dict(it.get("payload") or {})
            payload_obj["description"] = it["description"]
            payload_obj["event_spo"]   = list(it["event_spo"])
            payload_obj["task_type"]   = it["task_type"]
            payload_path = self.config.payload_dir / f"{local_uuid}.json"
            payload_path.write_text(json.dumps(payload_obj, sort_keys=True, default=str), encoding="utf-8")
            pendings.append(_Pending(
                local_uuid        = local_uuid,
                parent_local_uuid = it.get("parent_local_uuid"),
                task_type         = it["task_type"],
                description       = it["description"],
                event_spo         = it["event_spo"],
                payload_path      = payload_path,
            ))
            records.append({"local_uuid": local_uuid, "task_type": it["task_type"],
                            "description": it["description"]})
            local_uuids.append(local_uuid)
        with self._lock:
            for p in pendings:
                self._pending[p.local_uuid] = p
        self.journal.append("intent_start_batch", count=len(local_uuids), records=records)
        self._queue.enqueue(("start_batch", local_uuids))
        return local_uuids

    def complete_task_batch(self, items: list[Mapping[str, Any]]) -> None:
        """items: list of {"local_uuid": ..., "result": {...}}."""
        for it in items:
            local_uuid = it["local_uuid"]
            result_obj = dict(it.get("result") or {})
            result_path = self.config.payload_dir / f"{local_uuid}.result.json"
            result_path.write_text(json.dumps(result_obj, sort_keys=True, default=str), encoding="utf-8")
        self.journal.append("intent_complete_batch",
                            count=len(items),
                            local_uuids=[it["local_uuid"] for it in items])
        self._queue.enqueue(("complete_batch", [it["local_uuid"] for it in items]))

    def shutdown(self, timeout: float = 10.0) -> None:
        self._queue.shutdown(timeout=timeout)

    # ---------- internals -------------------------------------------------

    def _submit(self, item: tuple) -> None:
        kind = item[0]
        if self.config.mode == "dryrun":
            self.journal.append("dryrun_skipped", item=list(item))
            return

        try:
            self._ensure_web3()
        except Exception as exc:                              # noqa: BLE001
            log.warning("web3 init failed (%s); falling back to dryrun for this op", exc)
            self.journal.append("dryrun_fallback", item=list(item), error=str(exc))
            return

        if kind == "start":
            self._submit_start(item[1])
        elif kind == "complete":
            self._submit_complete(item[1])
        elif kind == "fail":
            self._submit_fail(item[1], item[2])
        elif kind == "atomic":
            self._submit_atomic(item[1])
        elif kind == "start_batch":
            self._submit_start_batch(item[1])
        elif kind == "complete_batch":
            self._submit_complete_batch(item[1])
        else:                                                 # pragma: no cover
            raise ValueError(f"unknown queue item kind: {kind!r}")

    def _submit_start(self, local_uuid: str) -> None:
        with self._lock:
            pending = self._pending[local_uuid]
            parent_onchain = (
                self._local_to_onchain.get(pending.parent_local_uuid)
                if pending.parent_local_uuid else 0
            ) or 0

        if pending.parent_local_uuid and parent_onchain == 0:
            # parent not yet confirmed; requeue.
            self._queue.enqueue(("start", local_uuid))
            return

        desc_hash  = keccak256_text(pending.description)
        event_text = "|".join(pending.event_spo)
        spo_hash   = keccak256_text(event_text)
        type_hash  = keccak256_text(pending.task_type)

        tx_hash, on_chain_id = self._call_start(parent_onchain, type_hash, desc_hash, spo_hash, "")

        with self._lock:
            self._local_to_onchain[local_uuid] = on_chain_id

        self.journal.append(
            "tx_start",
            local_uuid  = local_uuid,
            on_chain_id = on_chain_id,
            tx_hash     = tx_hash,
        )

    def _submit_complete(self, local_uuid: str) -> None:
        with self._lock:
            on_chain_id = self._local_to_onchain.get(local_uuid)
        if on_chain_id is None:
            self._queue.enqueue(("complete", local_uuid))
            return

        result_path = self.config.payload_dir / f"{local_uuid}.result.json"
        result_obj  = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {}
        result_hash = keccak256_json(result_obj)

        tx_hash = self._call_complete(on_chain_id, result_hash, "")
        self.journal.append(
            "tx_complete",
            local_uuid  = local_uuid,
            on_chain_id = on_chain_id,
            tx_hash     = tx_hash,
        )

    def _submit_fail(self, local_uuid: str, reason: str) -> None:
        with self._lock:
            on_chain_id = self._local_to_onchain.get(local_uuid)
        if on_chain_id is None:
            self._queue.enqueue(("fail", local_uuid, reason))
            return

        reason_hash = keccak256_text(reason)
        tx_hash = self._call_fail(on_chain_id, reason_hash)
        self.journal.append(
            "tx_fail",
            local_uuid  = local_uuid,
            on_chain_id = on_chain_id,
            tx_hash     = tx_hash,
        )

    def _submit_atomic(self, local_uuid: str) -> None:
        with self._lock:
            pending = self._pending[local_uuid]
            parent_onchain = (
                self._local_to_onchain.get(pending.parent_local_uuid)
                if pending.parent_local_uuid else 0
            ) or 0
        if pending.parent_local_uuid and parent_onchain == 0:
            self._queue.enqueue(("atomic", local_uuid))
            return

        desc_hash  = keccak256_text(pending.description)
        spo_hash   = keccak256_text("|".join(pending.event_spo))
        type_hash  = keccak256_text(pending.task_type)
        result_path = self.config.payload_dir / f"{local_uuid}.result.json"
        result_obj  = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {}
        result_hash = keccak256_json(result_obj)

        tx_hash, on_chain_id = self._call_atomic(parent_onchain, type_hash, desc_hash, spo_hash, result_hash, "")
        with self._lock:
            self._local_to_onchain[local_uuid] = on_chain_id
        self.journal.append("tx_atomic",
                            local_uuid=local_uuid, on_chain_id=on_chain_id, tx_hash=tx_hash)

    def _submit_start_batch(self, local_uuids: list[str]) -> None:
        with self._lock:
            pendings = [self._pending[u] for u in local_uuids]
            parent_resolutions = []
            for p in pendings:
                if p.parent_local_uuid:
                    parent_id = self._local_to_onchain.get(p.parent_local_uuid)
                    if parent_id is None:
                        # Any unresolved parent forces a re-queue of the whole batch.
                        self._queue.enqueue(("start_batch", local_uuids))
                        return
                    parent_resolutions.append(parent_id)
                else:
                    parent_resolutions.append(0)

        starts = []
        for p, parent_id in zip(pendings, parent_resolutions):
            starts.append({
                "parentId":        parent_id,
                "taskType":        keccak256_text(p.task_type),
                "descriptionHash": keccak256_text(p.description),
                "eventSPO":        keccak256_text("|".join(p.event_spo)),
                "cid":             "",
            })
        tx_hash, ids = self._call_start_batch(starts)
        with self._lock:
            for u, oid in zip(local_uuids, ids):
                self._local_to_onchain[u] = oid
        self.journal.append("tx_start_batch",
                            count=len(local_uuids),
                            local_uuids=local_uuids,
                            on_chain_ids=ids,
                            tx_hash=tx_hash)

    def _submit_many(self, items: list[tuple]) -> None:
        """Batch worker: group same-kind queue items into single txs.

        Called by TxQueue when batch_window_sec > 0. Items that don't
        coalesce well (fail, start_batch / complete_batch already-batched
        explicit calls) fall through to the per-item submit. start /
        atomic / complete coalesce: 2+ items of the same kind go through
        the V2 batch entry points.
        """
        if not items:
            return
        if self.config.mode == "dryrun":
            self.journal.append("dryrun_skipped_batch", count=len(items),
                                kinds=[it[0] for it in items])
            return
        try:
            self._ensure_web3()
        except Exception as exc:                              # noqa: BLE001
            log.warning("web3 init failed (%s); falling back to dryrun for %d items", exc, len(items))
            self.journal.append("dryrun_fallback_batch", count=len(items), error=str(exc))
            return

        starts:    list[str] = []
        atomics:   list[str] = []
        completes: list[str] = []
        misc:      list[tuple] = []
        for it in items:
            kind = it[0]
            if   kind == "start":           starts.append(it[1])
            elif kind == "atomic":          atomics.append(it[1])
            elif kind == "complete":        completes.append(it[1])
            else:                           misc.append(it)

        if len(starts) >= 2:
            self._submit_start_batch_grouped(starts)
        else:
            for u in starts: self._submit_start(u)

        if len(atomics) >= 2:
            self._submit_atomic_batch_grouped(atomics)
        else:
            for u in atomics: self._submit_atomic(u)

        if len(completes) >= 2:
            self._submit_complete_batch_grouped(completes)
        else:
            for u in completes: self._submit_complete(u)

        for it in misc:
            kind = it[0]
            if   kind == "fail":            self._submit_fail(it[1], it[2])
            elif kind == "start_batch":     self._submit_start_batch(it[1])
            elif kind == "complete_batch":  self._submit_complete_batch(it[1])
            else:                                                # pragma: no cover
                log.warning("tx-queue _submit_many: unknown kind %r", kind)

    def _submit_start_batch_grouped(self, local_uuids: list[str]) -> None:
        """Like _submit_start_batch but starts from individual `start` items.

        Items whose parents aren't yet on chain get re-enqueued individually
        so they don't poison the whole batch with a parent-not-found revert.
        """
        with self._lock:
            ready: list[str]   = []
            ready_pending      = []
            ready_parents      = []
            for u in local_uuids:
                p = self._pending[u]
                if p.parent_local_uuid:
                    parent_id = self._local_to_onchain.get(p.parent_local_uuid)
                    if parent_id is None:
                        self._queue.enqueue(("start", u))
                        continue
                else:
                    parent_id = 0
                ready.append(u)
                ready_pending.append(p)
                ready_parents.append(parent_id)
        if not ready:
            return
        if len(ready) == 1:
            self._submit_start(ready[0])
            return

        starts = [{
            "parentId":        parent_id,
            "taskType":        keccak256_text(p.task_type),
            "descriptionHash": keccak256_text(p.description),
            "eventSPO":        keccak256_text("|".join(p.event_spo)),
            "cid":             "",
        } for p, parent_id in zip(ready_pending, ready_parents)]
        tx_hash, ids = self._call_start_batch(starts)
        with self._lock:
            for u, oid in zip(ready, ids):
                self._local_to_onchain[u] = oid
        self.journal.append("tx_start_batch_grouped",
                            count=len(ready), local_uuids=ready,
                            on_chain_ids=ids, tx_hash=tx_hash)

    def _submit_atomic_batch_grouped(self, local_uuids: list[str]) -> None:
        with self._lock:
            ready: list[str] = []
            ready_pending    = []
            ready_parents    = []
            for u in local_uuids:
                p = self._pending[u]
                if p.parent_local_uuid:
                    parent_id = self._local_to_onchain.get(p.parent_local_uuid)
                    if parent_id is None:
                        self._queue.enqueue(("atomic", u))
                        continue
                else:
                    parent_id = 0
                ready.append(u)
                ready_pending.append(p)
                ready_parents.append(parent_id)
        if not ready:
            return
        if len(ready) == 1:
            self._submit_atomic(ready[0])
            return

        starts: list[dict] = []
        result_hashes: list[bytes] = []
        for u, p, parent_id in zip(ready, ready_pending, ready_parents):
            starts.append({
                "parentId":        parent_id,
                "taskType":        keccak256_text(p.task_type),
                "descriptionHash": keccak256_text(p.description),
                "eventSPO":        keccak256_text("|".join(p.event_spo)),
                "cid":             "",
            })
            result_path = self.config.payload_dir / f"{u}.result.json"
            result_obj  = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {}
            result_hashes.append(keccak256_json(result_obj))

        tx_hash, ids = self._call_atomic_batch(starts, result_hashes)
        with self._lock:
            for u, oid in zip(ready, ids):
                self._local_to_onchain[u] = oid
        self.journal.append("tx_atomic_batch_grouped",
                            count=len(ready), local_uuids=ready,
                            on_chain_ids=ids, tx_hash=tx_hash)

    def _submit_complete_batch_grouped(self, local_uuids: list[str]) -> None:
        with self._lock:
            ready: list[str] = []
            ready_ids: list[int] = []
            for u in local_uuids:
                oid = self._local_to_onchain.get(u)
                if oid is None:
                    self._queue.enqueue(("complete", u))
                    continue
                ready.append(u)
                ready_ids.append(oid)
        if not ready:
            return
        if len(ready) == 1:
            self._submit_complete(ready[0])
            return

        completes = []
        for u, oid in zip(ready, ready_ids):
            result_path = self.config.payload_dir / f"{u}.result.json"
            result_obj  = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {}
            completes.append({"id": oid, "resultHash": keccak256_json(result_obj), "cid": ""})
        tx_hash = self._call_complete_batch(completes)
        self.journal.append("tx_complete_batch_grouped",
                            count=len(ready), local_uuids=ready,
                            on_chain_ids=ready_ids, tx_hash=tx_hash)

    def _submit_complete_batch(self, local_uuids: list[str]) -> None:
        with self._lock:
            ids: list[int] = []
            for u in local_uuids:
                oid = self._local_to_onchain.get(u)
                if oid is None:
                    self._queue.enqueue(("complete_batch", local_uuids))
                    return
                ids.append(oid)

        completes = []
        for u, oid in zip(local_uuids, ids):
            result_path = self.config.payload_dir / f"{u}.result.json"
            result_obj  = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {}
            completes.append({
                "id":         oid,
                "resultHash": keccak256_json(result_obj),
                "cid":        "",
            })
        tx_hash = self._call_complete_batch(completes)
        self.journal.append("tx_complete_batch",
                            count=len(local_uuids),
                            local_uuids=local_uuids,
                            on_chain_ids=ids,
                            tx_hash=tx_hash)

    # ---------- web3 plumbing --------------------------------------------

    def _ensure_web3(self) -> None:
        if self._contract is not None:
            return
        from web3 import Web3                                           # type: ignore[import-not-found]
        from eth_account import Account                                  # type: ignore[import-not-found]

        w3 = Web3(Web3.HTTPProvider(self.config.rpc_url))
        if not w3.is_connected():
            raise RuntimeError(f"cannot connect to {self.config.rpc_url}")
        abi = json.loads(self.config.abi_path.read_text(encoding="utf-8"))
        contract = w3.eth.contract(
            address=Web3.to_checksum_address(self.config.contract_address),
            abi=abi,
        )
        self._w3       = w3
        self._account  = Account.from_key(self.config.private_key)
        self._contract = contract

    def _call_start(
        self,
        parent_id: int,
        task_type: bytes,
        desc_hash: bytes,
        spo_hash: bytes,
        cid: str,
    ) -> tuple[str, int]:
        fn = self._contract.functions.startTask(parent_id, task_type, desc_hash, spo_hash, cid)
        receipt = self._send(fn)
        # Decode TaskStarted event to extract the assigned id.
        events = self._contract.events.TaskStarted().process_receipt(receipt)
        if not events:                                                  # pragma: no cover
            raise RuntimeError("TaskStarted event missing from receipt")
        return receipt.transactionHash.hex(), int(events[0]["args"]["id"])

    def _call_complete(self, on_chain_id: int, result_hash: bytes, cid: str) -> str:
        fn = self._contract.functions.completeTask(on_chain_id, result_hash, cid)
        receipt = self._send(fn)
        return receipt.transactionHash.hex()

    def _call_fail(self, on_chain_id: int, reason_hash: bytes) -> str:
        fn = self._contract.functions.failTask(on_chain_id, reason_hash)
        receipt = self._send(fn)
        return receipt.transactionHash.hex()

    def _call_atomic(
        self,
        parent_id: int,
        task_type: bytes,
        desc_hash: bytes,
        spo_hash: bytes,
        result_hash: bytes,
        cid: str,
    ) -> tuple[str, int]:
        fn = self._contract.functions.recordAtomicAction(
            parent_id, task_type, desc_hash, spo_hash, result_hash, cid)
        receipt = self._send(fn)
        events = self._contract.events.AtomicActionRecorded().process_receipt(receipt)
        if not events:                                                  # pragma: no cover
            raise RuntimeError("AtomicActionRecorded event missing from receipt")
        return receipt.transactionHash.hex(), int(events[0]["args"]["id"])

    def _call_start_batch(self, items: list[dict]) -> tuple[str, list[int]]:
        # web3.py marshals dicts→tuples positionally for struct[] calldata.
        fn = self._contract.functions.startTaskBatch(items)
        receipt = self._send(fn)
        events = self._contract.events.TaskStarted().process_receipt(receipt)
        ids = [int(e["args"]["id"]) for e in events]
        return receipt.transactionHash.hex(), ids

    def _call_complete_batch(self, items: list[dict]) -> str:
        fn = self._contract.functions.completeTaskBatch(items)
        receipt = self._send(fn)
        return receipt.transactionHash.hex()

    def _call_atomic_batch(
        self,
        starts: list[dict],
        result_hashes: list[bytes],
    ) -> tuple[str, list[int]]:
        fn = self._contract.functions.recordAtomicActionBatch(starts, result_hashes)
        receipt = self._send(fn)
        events = self._contract.events.AtomicActionRecorded().process_receipt(receipt)
        ids = [int(e["args"]["id"]) for e in events]
        return receipt.transactionHash.hex(), ids

    def _send(self, fn):
        w3      = self._w3
        account = self._account
        nonce   = w3.eth.get_transaction_count(account.address)
        tx = fn.build_transaction({
            "from":     account.address,
            "nonce":    nonce,
            "chainId":  self.config.chain_id,
            "gas":      300_000,
            "gasPrice": w3.eth.gas_price,
        })
        signed = account.sign_transaction(tx)
        tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)
        return w3.eth.wait_for_transaction_receipt(tx_hash, timeout=self.config.tx_timeout_sec)
