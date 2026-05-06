"""Read-only HTTP API for the vending sandbox.

Lives in its own module so tests can import these views without dragging
in the legacy `translator.views` (which uses pre-3.0 Django imports).
"""

from __future__ import annotations

import json
import os

from django.http import JsonResponse


def _sim_storage_dir(sim_code: str) -> str:
    return os.path.join("storage", sim_code)


def _safe_sim(sim_code: str) -> str | None:
    if not sim_code or ".." in sim_code:
        return None
    if not all(c.isalnum() or c in "-_." for c in sim_code):
        return None
    d = _sim_storage_dir(sim_code)
    if not os.path.isdir(d):
        return None
    return d


def vending_state_api(request, sim_code: str):
    """Return Vendy's current VendingState plus per-sim metadata."""
    sim_dir = _safe_sim(sim_code)
    if sim_dir is None:
        return JsonResponse({"error": "sim not found"}, status=404)

    meta = {}
    meta_path = os.path.join(sim_dir, "reverie", "meta.json")
    if os.path.exists(meta_path):
        with open(meta_path) as f:
            meta = json.load(f)

    vending_state = None
    vending_state_path = None
    personas_dir = os.path.join(sim_dir, "personas")
    if os.path.isdir(personas_dir):
        for persona_name in sorted(os.listdir(personas_dir)):
            candidate = os.path.join(personas_dir, persona_name,
                                     "bootstrap_memory", "vending_state.json")
            if os.path.exists(candidate):
                vending_state_path = candidate
                with open(candidate) as f:
                    vending_state = json.load(f)
                vending_state["__persona"] = persona_name
                break

    return JsonResponse({
        "sim_code":      sim_code,
        "maze_name":     meta.get("maze_name"),
        "curr_time":     meta.get("curr_time"),
        "step":          meta.get("step"),
        "vending_state": vending_state,
        "source_path":   vending_state_path,
    })


def vending_journal_api(request, sim_code: str):
    """Tail blockchain_journal.jsonl. Pass ?since=<int> to skip old entries."""
    sim_dir = _safe_sim(sim_code)
    if sim_dir is None:
        return JsonResponse({"error": "sim not found"}, status=404)

    try:
        since = int(request.GET.get("since", "0"))
    except (TypeError, ValueError):
        since = 0

    journal_path = os.path.join(sim_dir, "blockchain_journal.jsonl")
    entries: list[dict] = []
    if os.path.exists(journal_path):
        with open(journal_path) as f:
            for i, line in enumerate(f):
                if i < since:
                    continue
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    return JsonResponse({
        "sim_code": sim_code,
        "since":    since,
        "count":    len(entries),
        "entries":  entries,
    })


def vending_personas_api(request, sim_code: str):
    """Per-persona current tile + role from the latest environment snapshot."""
    sim_dir = _safe_sim(sim_code)
    if sim_dir is None:
        return JsonResponse({"error": "sim not found"}, status=404)

    env_dir = os.path.join(sim_dir, "environment")
    if not os.path.isdir(env_dir):
        return JsonResponse({"sim_code": sim_code, "personas": []})

    steps = [int(f.split(".")[0]) for f in os.listdir(env_dir) if f.endswith(".json")]
    if not steps:
        return JsonResponse({"sim_code": sim_code, "personas": []})

    latest_step = max(steps)
    with open(os.path.join(env_dir, f"{latest_step}.json")) as f:
        env_snapshot = json.load(f)

    personas: list[dict] = []
    for name, info in env_snapshot.items():
        bm = os.path.join(sim_dir, "personas", name, "bootstrap_memory")
        role = "vendor"
        try:
            scratch_path = os.path.join(bm, "scratch.json")
            if os.path.exists(scratch_path):
                with open(scratch_path) as f:
                    scratch = json.load(f)
                role = scratch.get("role", role)
            if role == "vendor" and not os.path.exists(os.path.join(bm, "vending_state.json")):
                role = "customer"
        except (OSError, json.JSONDecodeError):
            pass
        personas.append({
            "name": name,
            "x":    info.get("x"),
            "y":    info.get("y"),
            "role": role,
        })

    return JsonResponse({
        "sim_code": sim_code,
        "step":     latest_step,
        "personas": personas,
    })
