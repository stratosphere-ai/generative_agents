"""End-to-end check for the embedding seeder: produce files, then load via upstream AssociativeMemory."""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND   = REPO_ROOT / "reverie" / "backend_server"


def test_stub_embedding_is_deterministic_and_unit_norm():
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import seed_embeddings as se

    v1 = se.stub_embedding("hello")
    v2 = se.stub_embedding("hello")
    v3 = se.stub_embedding("world")
    assert v1 == v2
    assert v1 != v3
    assert len(v1) == 1536
    norm = sum(x * x for x in v1) ** 0.5
    assert abs(norm - 1.0) < 1e-5


def test_seeder_round_trips_through_associative_memory(tmp_path, monkeypatch):
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import seed_embeddings as se

    seeds = [
        {"description": "A", "subject": "X", "predicate": "is", "object": "alpha",
         "keywords": ["a"], "poignancy": 5},
        {"description": "B", "subject": "X", "predicate": "is", "object": "beta",
         "keywords": ["b"], "poignancy": 6},
    ]
    seed_path = tmp_path / "seeds.json"
    seed_path.write_text(json.dumps(seeds))

    persona_dir = tmp_path / "Persona"
    persona_dir.mkdir()

    monkeypatch.setattr(sys, "argv", [
        "seed_embeddings.py",
        "--seeds",       str(seed_path),
        "--persona-dir", str(persona_dir),
        "--created",     "2026-04-28T00:00:00",
    ])
    se.main()

    am_dir = persona_dir / "bootstrap_memory" / "associative_memory"
    assert (am_dir / "nodes.json").exists()
    assert (am_dir / "embeddings.json").exists()
    assert (am_dir / "kw_strength.json").exists()

    # Load via upstream AssociativeMemory to confirm schema compatibility.
    utils = types.ModuleType("utils")
    utils.openai_api_key = "sk-stub"
    utils.key_owner      = "test"
    utils.maze_assets_loc = str(REPO_ROOT / "environment" / "frontend_server" / "static_dirs" / "assets")
    utils.env_matrix      = f"{utils.maze_assets_loc}/the_ville/matrix"
    utils.env_visuals     = f"{utils.maze_assets_loc}/the_ville/visuals"
    utils.fs_storage      = str(REPO_ROOT / "environment" / "frontend_server" / "storage")
    utils.fs_temp_storage = str(REPO_ROOT / "environment" / "frontend_server" / "temp_storage")
    utils.collision_block_id = "32125"
    utils.debug = False
    monkeypatch.setitem(sys.modules, "utils", utils)
    monkeypatch.syspath_prepend(str(BACKEND))

    if "persona.memory_structures.associative_memory" in sys.modules:
        del sys.modules["persona.memory_structures.associative_memory"]
    from persona.memory_structures.associative_memory import AssociativeMemory

    am = AssociativeMemory(str(am_dir))
    assert len(am.id_to_node) == 2
    descs = sorted(n.description for n in am.id_to_node.values())
    assert descs == ["A", "B"]
    assert am.kw_strength_thought == {"a": 1, "b": 1}
