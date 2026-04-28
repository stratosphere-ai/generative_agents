"""Generate a Central-Plaza maze for the vending sandbox.

Writes the five CSV layers + maze_meta_info.json + the 5 special_blocks CSVs
under environment/frontend_server/static_dirs/assets/vending/matrix/.

The CSV format matches the upstream Maze loader so we don't need to patch maze.py.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT  = ROOT / "environment/frontend_server/static_dirs/assets/vending/matrix"

WORLD_NAME = "plaza_world"
SECTOR     = "Central Plaza"

WIDTH  = 50
HEIGHT = 35

# Marker numbers: cell values in maze CSVs are looked up in special_blocks
# dicts. "0" means blank. We pick non-overlapping integer ranges per layer.
WORLD_MARKER     = 25280
SECTOR_MARKER    = 25281
ARENA_MARKER_BASE = 25282        # first arena, +1 per arena
COLLISION_MARKER = 25287
OBJECT_MARKER_BASE = 32000
SPAWN_MARKER_BASE  = 33000

# arena rectangles: (top, left, bottom_excl, right_excl, name)
ARENAS = [
    (5,  3,  15, 13, "Vending Corner"),
    (5,  14, 30, 36, "Plaza Square"),
    (1,  0,  5,  WIDTH, "North Walkway"),
    (30, 0,  HEIGHT, WIDTH, "South Walkway"),
    (5,  37, 30, WIDTH, "Cafe Sidewalk"),
]

# game objects placed at specific tiles: (row, col, name, arena)
GAME_OBJECTS = [
    (10, 5,  "VendingMachine", "Vending Corner"),
    (12, 5,  "RestockBin",     "Vending Corner"),
    (10, 7,  "PowerOutlet",    "Vending Corner"),
    (15, 22, "Bench",          "Plaza Square"),
    (18, 28, "Bench",          "Plaza Square"),
    (20, 25, "Fountain",       "Plaza Square"),
    (12, 18, "StreetLamp",     "Plaza Square"),
    (25, 30, "StreetLamp",     "Plaza Square"),
]

# spawning locations: (row, col, name)
SPAWNS = [
    (2,  5,  "plaza_north_entry"),
    (33, 45, "plaza_south_entry"),
    (10, 5,  "vending_spawn"),
]


def _blank() -> list[list[str]]:
    return [["0" for _ in range(WIDTH)] for _ in range(HEIGHT)]


def _write_csv(path: Path, grid: list[list[str]]) -> None:
    """Upstream maze.py reads cell data as a SINGLE row of width*height cells.

    We flatten row-major (matches the Tiled export the upstream uses) and
    join with ', ' so the existing csv reader splits cleanly.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    flat = [cell for row in grid for cell in row]
    path.write_text(", ".join(flat))


def build() -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    sector = _blank()
    arena  = _blank()
    obj    = _blank()
    spawn  = _blank()
    coll   = _blank()

    # Stamp sector + arena markers across each rectangle.
    for idx, (top, left, bottom, right, _name) in enumerate(ARENAS):
        marker = str(ARENA_MARKER_BASE + idx)
        for r in range(top, bottom):
            for c in range(left, right):
                sector[r][c] = str(SECTOR_MARKER)
                arena[r][c]  = marker

    for i, (r, c, _name, _arena) in enumerate(GAME_OBJECTS):
        obj[r][c]  = str(OBJECT_MARKER_BASE + i)
        coll[r][c] = str(COLLISION_MARKER)  # objects are obstacles

    for i, (r, c, _name) in enumerate(SPAWNS):
        spawn[r][c] = str(SPAWN_MARKER_BASE + i)

    # Border collisions
    for r in range(HEIGHT):
        coll[r][0] = str(COLLISION_MARKER)
        coll[r][WIDTH - 1] = str(COLLISION_MARKER)
    for c in range(WIDTH):
        coll[0][c] = str(COLLISION_MARKER)
        coll[HEIGHT - 1][c] = str(COLLISION_MARKER)

    _write_csv(OUT / "maze/sector_maze.csv",            sector)
    _write_csv(OUT / "maze/arena_maze.csv",             arena)
    _write_csv(OUT / "maze/game_object_maze.csv",       obj)
    _write_csv(OUT / "maze/spawning_location_maze.csv", spawn)
    _write_csv(OUT / "maze/collision_maze.csv",         coll)

    # special_blocks: one block-marker per line; columns are
    # marker, world[, sector[, arena[, name]]]
    sb = OUT / "special_blocks"
    sb.mkdir(parents=True, exist_ok=True)
    (sb / "world_blocks.csv").write_text(f"{WORLD_MARKER}, {WORLD_NAME}\n")
    (sb / "sector_blocks.csv").write_text(f"{SECTOR_MARKER}, {WORLD_NAME}, {SECTOR}\n")
    arena_lines = [
        f"{ARENA_MARKER_BASE + i}, {WORLD_NAME}, {SECTOR}, {a[4]}"
        for i, a in enumerate(ARENAS)
    ]
    (sb / "arena_blocks.csv").write_text("\n".join(arena_lines) + "\n")
    obj_lines = [
        f"{OBJECT_MARKER_BASE + i}, {WORLD_NAME}, {SECTOR}, {arena_name}, {name}"
        for i, (_r, _c, name, arena_name) in enumerate(GAME_OBJECTS)
    ]
    (sb / "game_object_blocks.csv").write_text("\n".join(obj_lines) + "\n")
    spawn_lines = [
        f"{SPAWN_MARKER_BASE + i}, {WORLD_NAME}, {SECTOR}, , {s[2]}"
        for i, s in enumerate(SPAWNS)
    ]
    (sb / "spawning_location_blocks.csv").write_text("\n".join(spawn_lines) + "\n")

    meta = {
        "world_name":   WORLD_NAME,
        "maze_width":   WIDTH,
        "maze_height":  HEIGHT,
        "sq_tile_size": 32,
        "special_constraint": "",
    }
    (OUT / "maze_meta_info.json").write_text(json.dumps(meta, indent=2))

    print(f"[maze] wrote {OUT}")


if __name__ == "__main__":
    build()
