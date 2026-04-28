"""Validate that the build_maze.py output parses cleanly via the upstream Maze class.

This test stubs `utils` with the project-relative paths the upstream code expects,
then instantiates Maze("vending") and checks key tile properties.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND   = REPO_ROOT / "reverie" / "backend_server"


@pytest.fixture()
def stubbed_utils(monkeypatch, tmp_path):
    """Inject a fake `utils` module with the paths the_ville hardcodes."""
    assets = REPO_ROOT / "environment" / "frontend_server" / "static_dirs" / "assets"
    storage = REPO_ROOT / "environment" / "frontend_server" / "storage"

    utils = types.ModuleType("utils")
    utils.maze_assets_loc    = str(assets)
    utils.env_matrix         = f"{assets}/the_ville/matrix"
    utils.env_visuals        = f"{assets}/the_ville/visuals"
    utils.fs_storage         = str(storage)
    utils.fs_temp_storage    = str(REPO_ROOT / "environment" / "frontend_server" / "temp_storage")
    utils.collision_block_id = "32125"
    utils.openai_api_key     = "sk-stub"
    utils.key_owner          = "test"
    utils.debug              = False
    monkeypatch.setitem(sys.modules, "utils", utils)
    monkeypatch.syspath_prepend(str(BACKEND))
    yield utils


def test_vending_maze_loads(stubbed_utils):
    # Force-rebuild the maze to keep this test self-contained.
    import importlib.util
    spec = importlib.util.spec_from_file_location("build_maze", REPO_ROOT / "scripts" / "build_maze.py")
    bm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bm)
    bm.build()

    # Now load the Maze class from the upstream backend.
    if "maze" in sys.modules:
        del sys.modules["maze"]
    from maze import Maze

    m = Maze("vending")
    assert m.maze_width == 50
    assert m.maze_height == 35
    assert len(m.tiles) == 35
    assert len(m.tiles[0]) == 50

    # Tile (10, 5) should be the VendingMachine inside Vending Corner.
    t = m.tiles[10][5]
    assert t["sector"]      == "Central Plaza"
    assert t["arena"]       == "Vending Corner"
    assert t["game_object"] == "VendingMachine"

    # Tile (15, 22) is a Bench inside Plaza Square.
    t2 = m.tiles[15][22]
    assert t2["arena"]       == "Plaza Square"
    assert t2["game_object"] == "Bench"

    # Spawn locations resolve.
    t_spawn = m.tiles[2][5]
    assert t_spawn["spawning_location"] == "plaza_north_entry"

    # Border collision.
    assert m.tiles[0][0]["collision"] is True
    # Interior plaza tile is walkable.
    assert m.tiles[10][20]["collision"] is False
