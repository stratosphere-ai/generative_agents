"""Generate a minimal Tiled-format JSON tilemap for Central Plaza.

The Phaser frontend in main_script.html consumes a Tiled JSON to render the
world. We reuse the_ville's tilesets (no new PNGs needed) so we only need to
produce a tilemap with matching layer names and meaningful tile GIDs.

Output: assets/vending/visuals/plaza.json
"""

from __future__ import annotations

import json
from pathlib import Path

# Re-import the maze layout so the visuals stay consistent with the backend.
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_maze import (                                           # noqa: E402
    ARENAS, GAME_OBJECTS, SPAWNS,
    WIDTH, HEIGHT,
    SECTOR_MARKER, ARENA_MARKER_BASE, OBJECT_MARKER_BASE, SPAWN_MARKER_BASE,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
OUT       = REPO_ROOT / "environment/frontend_server/static_dirs/assets/vending/visuals/plaza.json"


# These GIDs are real positions inside the_ville tilesets and are stable across
# the upstream repo. We use a basic grass tile for the plaza ground and the
# blocks tileset for collisions / markers (mirrors the_ville's pattern).
GID_GRASS         = 2          # CuteRPG_Field_B index 1
GID_PAVEMENT      = 22         # CuteRPG_Field_B variant
GID_TREE          = 9061       # CuteRPG_Village_B sample tile (decorative)
GID_BENCH         = 10881      # interiors_pt1 sample
GID_FOUNTAIN      = 10897      # interiors_pt1 sample
GID_VENDING_MACH  = 10913      # interiors_pt1 sample (visually a column)
GID_COLLISION     = 32125      # blocks tileset entry 1 -- the canonical collision block
GID_WORLD         = 32134      # blocks tileset entry -- world marker

# Tilesets we reference. firstgid order MUST match the_ville_jan7.json so the
# Phaser side resolves names identically.
TILESETS = [
    {"firstgid": 1,     "source_image": "CuteRPG_Field_B"},
    {"firstgid": 257,   "source_image": "CuteRPG_Field_C"},
    {"firstgid": 513,   "source_image": "CuteRPG_Harbor_C"},
    {"firstgid": 769,   "source_image": "Room_Builder_32x32"},
    {"firstgid": 9053,  "source_image": "CuteRPG_Village_B"},
    {"firstgid": 9309,  "source_image": "CuteRPG_Forest_B"},
    {"firstgid": 9565,  "source_image": "CuteRPG_Desert_C"},
    {"firstgid": 9821,  "source_image": "CuteRPG_Mountains_B"},
    {"firstgid": 10077, "source_image": "CuteRPG_Desert_B"},
    {"firstgid": 10333, "source_image": "CuteRPG_Forest_C"},
    {"firstgid": 10589, "source_image": "interiors_pt1"},
    {"firstgid": 15597, "source_image": "interiors_pt2"},
    {"firstgid": 20605, "source_image": "interiors_pt3"},
    {"firstgid": 25613, "source_image": "interiors_pt4"},
    {"firstgid": 27613, "source_image": "interiors_pt5"},
    {"firstgid": 32125, "source_image": "blocks"},
    {"firstgid": 32205, "source_image": "blocks_2"},
    {"firstgid": 32285, "source_image": "blocks_3"},
]

# Layer name list matches main_script.html createLayer() calls; layers we
# don't need are emitted empty so addTilesetImage / createLayer don't error.
LAYER_NAMES = [
    "Bottom Ground",
    "Exterior Ground",
    "Exterior Decoration L1",
    "Exterior Decoration L2",
    "Interior Ground",
    "Wall",
    "Interior Furniture L1",
    "Interior Furniture L2 ",   # trailing space matches upstream
    "Foreground L1",
    "Foreground L2",
    "Collisions",
    "Object Interaction Blocks",
    "Arena Blocks",
    "Sector Blocks",
    "World Blocks",
    "Spawning Blocks",
    "Special Blocks Registry",
]


def _zero_grid() -> list[int]:
    return [0] * (WIDTH * HEIGHT)


def _idx(r: int, c: int) -> int:
    return r * WIDTH + c


def _arena_mask() -> set[tuple[int, int]]:
    cells: set[tuple[int, int]] = set()
    for top, left, bottom, right, _name in ARENAS:
        for r in range(top, bottom):
            for c in range(left, right):
                cells.add((r, c))
    return cells


def build() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)

    layers: dict[str, list[int]] = {name: _zero_grid() for name in LAYER_NAMES}

    arena_cells = _arena_mask()

    # Bottom Ground: grass everywhere; pavement on walkways for variety.
    for r in range(HEIGHT):
        for c in range(WIDTH):
            layers["Bottom Ground"][_idx(r, c)] = GID_GRASS
    for top, left, bottom, right, name in ARENAS:
        if "Walkway" in name:
            gid = GID_PAVEMENT
        else:
            continue
        for r in range(top, bottom):
            for c in range(left, right):
                layers["Bottom Ground"][_idx(r, c)] = gid

    # Game-object visuals + collision.
    obj_gid_map = {
        "VendingMachine": GID_VENDING_MACH,
        "RestockBin":     GID_VENDING_MACH,
        "PowerOutlet":    GID_VENDING_MACH,
        "Bench":          GID_BENCH,
        "Fountain":       GID_FOUNTAIN,
        "StreetLamp":     GID_TREE,
    }
    for r, c, name, _arena in GAME_OBJECTS:
        layers["Interior Furniture L1"][_idx(r, c)] = obj_gid_map.get(name, GID_BENCH)
        layers["Collisions"][_idx(r, c)]            = GID_COLLISION

    # Border collision.
    for r in range(HEIGHT):
        layers["Collisions"][_idx(r, 0)]         = GID_COLLISION
        layers["Collisions"][_idx(r, WIDTH - 1)] = GID_COLLISION
    for c in range(WIDTH):
        layers["Collisions"][_idx(0, c)]          = GID_COLLISION
        layers["Collisions"][_idx(HEIGHT - 1, c)] = GID_COLLISION

    # Backend-aligned marker layers (these mirror the CSV maze).
    for r, c in arena_cells:
        layers["Sector Blocks"][_idx(r, c)] = SECTOR_MARKER
    for idx, (top, left, bottom, right, _name) in enumerate(ARENAS):
        for r in range(top, bottom):
            for c in range(left, right):
                layers["Arena Blocks"][_idx(r, c)] = ARENA_MARKER_BASE + idx
    for i, (r, c, _name, _arena) in enumerate(GAME_OBJECTS):
        layers["Object Interaction Blocks"][_idx(r, c)] = OBJECT_MARKER_BASE + i
    for i, (r, c, _name) in enumerate(SPAWNS):
        layers["Spawning Blocks"][_idx(r, c)] = SPAWN_MARKER_BASE + i
    for r in range(HEIGHT):
        for c in range(WIDTH):
            layers["World Blocks"][_idx(r, c)] = GID_WORLD

    # Assemble Tiled JSON.
    tiled_layers = []
    for i, name in enumerate(LAYER_NAMES, start=1):
        tiled_layers.append({
            "id":      i,
            "name":    name,
            "type":    "tilelayer",
            "visible": True,
            "opacity": 1,
            "x": 0, "y": 0,
            "width":  WIDTH,
            "height": HEIGHT,
            "data":   layers[name],
        })

    out = {
        "compressionlevel": -1,
        "width":  WIDTH,
        "height": HEIGHT,
        "infinite": False,
        "tilewidth":  32,
        "tileheight": 32,
        "orientation": "orthogonal",
        "renderorder": "right-down",
        "type": "map",
        "version": "1.10",
        "tiledversion": "1.10.2",
        "nextlayerid":  len(LAYER_NAMES) + 1,
        "nextobjectid": 1,
        "tilesets":   TILESETS,
        "layers":     tiled_layers,
    }

    OUT.write_text(json.dumps(out))
    print(f"[plaza-tilemap] wrote {OUT}  ({WIDTH}x{HEIGHT}, {len(LAYER_NAMES)} layers)")


if __name__ == "__main__":
    build()
