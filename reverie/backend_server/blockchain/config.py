"""Environment-driven configuration for the on-chain TaskRegistry client."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


Mode = Literal["live", "mock", "dryrun"]


@dataclass(frozen=True)
class BlockchainConfig:
    mode: Mode
    rpc_url: str
    chain_id: int
    private_key: str
    contract_address: str
    abi_path: Path
    journal_path: Path
    payload_dir: Path
    confirm_blocks: int = 1
    tx_timeout_sec: float = 30.0


def _env(name: str, default: str | None = None, *, required: bool = False) -> str:
    val = os.environ.get(name, default)
    if required and not val:
        raise RuntimeError(f"environment variable {name} is required")
    return val or ""


def load_config(*, sim_storage_dir: Path) -> BlockchainConfig:
    mode = _env("BLOCKCHAIN_MODE", "mock").lower()
    if mode not in ("live", "mock", "dryrun"):
        raise ValueError(f"BLOCKCHAIN_MODE must be live|mock|dryrun, got {mode!r}")

    abi_path = Path(_env(
        "TASK_REGISTRY_ABI",
        str(Path(__file__).resolve().parent / "abi" / "TaskRegistry.json"),
    ))

    journal_path = sim_storage_dir / "blockchain_journal.jsonl"
    payload_dir  = sim_storage_dir / "task_payloads"

    return BlockchainConfig(
        mode             = mode,                                          # type: ignore[arg-type]
        rpc_url          = _env("RPC_URL",          "http://127.0.0.1:8545"),
        chain_id         = int(_env("CHAIN_ID",     "31337")),
        private_key      = _env("PRIVATE_KEY",      "", required=(mode == "live")),
        contract_address = _env("CONTRACT_ADDRESS", "", required=(mode == "live")),
        abi_path         = abi_path,
        journal_path     = journal_path,
        payload_dir      = payload_dir,
        confirm_blocks   = int(_env("CONFIRM_BLOCKS", "1")),
        tx_timeout_sec   = float(_env("TX_TIMEOUT_SEC", "30")),
    )
