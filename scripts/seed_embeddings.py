"""Seed a persona's bootstrap_memory with thought nodes + embeddings.

Usage:
    python scripts/seed_embeddings.py \
        --seeds path/to/seeds.json \
        --persona-dir environment/frontend_server/storage/base_vending_min/personas/Vendy\\ Unit-001

`seeds.json` is a list of dicts:
    [
      {"description": "I am vending unit 001, an autonomous economic agent.",
       "subject": "Vendy Unit-001", "predicate": "is", "object": "an economic agent",
       "keywords": ["self", "agent", "economic"], "poignancy": 6},
      ...
    ]

Embedding source:
    - If OPENAI_API_KEY is set AND --online is passed, real ada-002 embeddings.
    - Otherwise: deterministic stub vectors (md5-seeded 1536-dim floats),
      stable across runs. Fine for local sims; for real semantic retrieval
      you want the online mode.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import random
from pathlib import Path
from typing import Any


EMBEDDING_DIM = 1536


def stub_embedding(text: str) -> list[float]:
    """Deterministic 1536-dim unit-norm vector seeded by md5(text).

    Not semantically meaningful, but stable across runs so retrieval over
    seeded thoughts is reproducible without OpenAI calls.
    """
    seed = int.from_bytes(hashlib.md5(text.encode("utf-8")).digest()[:8], "big")
    rng  = random.Random(seed)
    vec  = [rng.gauss(0.0, 1.0) for _ in range(EMBEDDING_DIM)]
    norm = sum(x * x for x in vec) ** 0.5 or 1.0
    return [x / norm for x in vec]


def online_embedding(text: str) -> list[float]:
    import openai                                                # type: ignore[import-not-found]
    return openai.Embedding.create(
        input=[text], model="text-embedding-ada-002",
    )["data"][0]["embedding"]


def _format_dt(dt: _dt.datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def build_nodes(seeds: list[dict], created: _dt.datetime) -> dict[str, dict[str, Any]]:
    nodes: dict[str, dict[str, Any]] = {}
    for i, seed in enumerate(seeds, start=1):
        node_id     = f"node_{i}"
        description = seed["description"]
        keywords    = list(seed.get("keywords") or [])
        nodes[node_id] = {
            "node_count":   i,
            "type_count":   i,
            "type":         "thought",
            "depth":        0,
            "created":      _format_dt(created),
            "expiration":   None,
            "subject":      seed.get("subject") or seed.get("s") or "",
            "predicate":    seed.get("predicate") or seed.get("p") or "is",
            "object":       seed.get("object") or seed.get("o") or "",
            "description":  description,
            "embedding_key": description,
            "poignancy":    int(seed.get("poignancy", 5)),
            "keywords":     keywords,
            "filling":      [],
        }
    return nodes


def build_kw_strength(seeds: list[dict]) -> dict[str, Any]:
    kw_strength_thought: dict[str, int] = {}
    for seed in seeds:
        for kw in seed.get("keywords") or []:
            kw_strength_thought[kw] = kw_strength_thought.get(kw, 0) + 1
    return {"kw_strength_event": {}, "kw_strength_thought": kw_strength_thought}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds",       required=True, type=Path)
    parser.add_argument("--persona-dir", required=True, type=Path)
    parser.add_argument("--online",      action="store_true",
                        help="Use real OpenAI embeddings (requires OPENAI_API_KEY).")
    parser.add_argument("--created",     default=None,
                        help="ISO datetime stamped on every seeded node.")
    args = parser.parse_args()

    seeds = json.loads(args.seeds.read_text(encoding="utf-8"))
    if not isinstance(seeds, list) or not seeds:
        raise SystemExit("seeds file must contain a non-empty JSON list")

    if args.online and not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("--online requires OPENAI_API_KEY in the environment")

    embed = online_embedding if args.online else stub_embedding

    am_dir = args.persona_dir / "bootstrap_memory" / "associative_memory"
    am_dir.mkdir(parents=True, exist_ok=True)

    created = (
        _dt.datetime.fromisoformat(args.created) if args.created
        else _dt.datetime.now().replace(microsecond=0)
    )

    nodes = build_nodes(seeds, created)
    embeddings = {seed["description"]: embed(seed["description"]) for seed in seeds}
    kw = build_kw_strength(seeds)

    (am_dir / "nodes.json").write_text(json.dumps(nodes, indent=2))
    (am_dir / "embeddings.json").write_text(json.dumps(embeddings))
    (am_dir / "kw_strength.json").write_text(json.dumps(kw, indent=2))

    print(f"[seed] wrote {len(nodes)} nodes -> {am_dir}")
    print(f"[seed] embedding source: {'openai' if args.online else 'stub-md5'}")


if __name__ == "__main__":
    main()
