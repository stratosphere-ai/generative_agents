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
    return [["" for _ in range(WIDTH)] for _ in range(HEIGHT)]


def _write_csv(path: Path, grid: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        for row in grid:
            writer.writerow(row)


def build() -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    sector = _blank()
    arena  = _blank()
    obj    = _blank()
    spawn  = _blank()
    coll   = _blank()

    # sector: every walkable tile carries the sector name; obstacles stay empty.
    for top, left, bottom, right, name in ARENAS:
        for r in range(top, bottom):
            for c in range(left, right):
                sector[r][c] = SECTOR
                arena[r][c]  = name

    for r, c, name, _arena in GAME_OBJECTS:
        obj[r][c] = name
        coll[r][c] = "X"  # objects are obstacles

    for r, c, name in SPAWNS:
        spawn[r][c] = name

    # Border collisions
    for r in range(HEIGHT):
        coll[r][0] = "X"
        coll[r][WIDTH - 1] = "X"
    for c in range(WIDTH):
        coll[0][c] = "X"
        coll[HEIGHT - 1][c] = "X"

    _write_csv(OUT / "maze/sector_maze.csv",            sector)
    _write_csv(OUT / "maze/arena_maze.csv",             arena)
    _write_csv(OUT / "maze/game_object_maze.csv",       obj)
    _write_csv(OUT / "maze/spawning_location_maze.csv", spawn)
    _write_csv(OUT / "maze/collision_maze.csv",         coll)

    # special_blocks
    sb = OUT / "special_blocks"
    sb.mkdir(parents=True, exist_ok=True)
    (sb / "world_blocks.csv").write_text(f"25280, {WORLD_NAME}\n")
    (sb / "sector_blocks.csv").write_text(f"25281, {WORLD_NAME}, {SECTOR}\n")
    arena_lines = [f"{25282 + i}, {WORLD_NAME}, {SECTOR}, {a[4]}" for i, a in enumerate(ARENAS)]
    (sb / "arena_blocks.csv").write_text("\n".join(arena_lines) + "\n")
    obj_lines = []
    for i, (_r, _c, name, arena_name) in enumerate(GAME_OBJECTS):
        obj_lines.append(f"{32000 + i}, {WORLD_NAME}, {SECTOR}, {arena_name}, {name}")
    (sb / "game_object_blocks.csv").write_text("\n".join(obj_lines) + "\n")
    spawn_lines = [f"{33000 + i}, {WORLD_NAME}, {SECTOR}, , {s[2]}" for i, s in enumerate(SPAWNS)]
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
