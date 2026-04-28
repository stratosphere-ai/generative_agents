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
        self._queue   = TxQueue(worker=self._submit, journal=self.journal)
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
