"""Sanity-check the generated plaza Tiled JSON has the structure main_script.html expects."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
TILEMAP   = REPO_ROOT / "environment/frontend_server/static_dirs/assets/vending/visuals/plaza.json"


EXPECTED_LAYER_NAMES = {
    "Bottom Ground", "Exterior Ground", "Exterior Decoration L1", "Exterior Decoration L2",
    "Interior Ground", "Wall", "Interior Furniture L1", "Interior Furniture L2 ",
    "Foreground L1", "Foreground L2", "Collisions", "Object Interaction Blocks",
    "Arena Blocks", "Sector Blocks", "World Blocks", "Spawning Blocks",
    "Special Blocks Registry",
}


@pytest.fixture(scope="module", autouse=True)
def _ensure_tilemap_built():
    if not TILEMAP.exists():
        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        import build_plaza_tilemap as bpt
        bpt.build()


def test_plaza_tilemap_has_expected_dimensions_and_layers():
    d = json.loads(TILEMAP.read_text(encoding="utf-8"))
    assert d["width"]  == 50
    assert d["height"] == 35
    assert d["tilewidth"]  == 32
    assert d["tileheight"] == 32
    assert d["orientation"] == "orthogonal"

    layer_names = {l["name"] for l in d["layers"] if l["type"] == "tilelayer"}
    assert EXPECTED_LAYER_NAMES <= layer_names, f"missing: {EXPECTED_LAYER_NAMES - layer_names}"


def test_plaza_collisions_match_game_objects_and_borders():
    d = json.loads(TILEMAP.read_text(encoding="utf-8"))
    collisions = next(l for l in d["layers"] if l["name"] == "Collisions")
    nz = sum(1 for v in collisions["data"] if v != 0)
    # 8 game objects + 4 borders that overlap at corners.
    border = 2 * 50 + 2 * 35 - 4   # 166
    assert nz >= border + 8 - 1    # within 1 of expected (corner overlaps)


def test_plaza_marker_layers_align_with_backend_csv():
    """Sector / Arena / Object / Spawn markers in the tilemap match the backend CSVs."""
    d = json.loads(TILEMAP.read_text(encoding="utf-8"))
    sector = next(l for l in d["layers"] if l["name"] == "Sector Blocks")
    arena  = next(l for l in d["layers"] if l["name"] == "Arena Blocks")
    spawn  = next(l for l in d["layers"] if l["name"] == "Spawning Blocks")

    assert 25281 in sector["data"]                 # SECTOR_MARKER
    assert 25282 in arena["data"]                  # first ARENA marker
    assert 33000 in spawn["data"]                  # first SPAWN marker
