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


def vending_dispatch_api(request, sim_code: str):
    """Dry-run: parse an LLM action and report what the gate would do.

    Accepts a POST body shaped either as `{"text": "<llm output>"}` or
    `{"action": "PRICE_CHANGE", ...}`. Loads the persona's sla_state.json
    + price_governance.json + supplier_ledger.json + vending_state.json
    from disk, runs the assessment, and returns the would-be result. Does
    NOT mutate any state — apply still requires the live sim's LLM loop.
    """
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    sim_dir = _safe_sim(sim_code)
    if sim_dir is None:
        return JsonResponse({"error": "sim not found"}, status=404)

    try:
        body = json.loads(request.body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return JsonResponse({"error": f"invalid JSON body: {exc}"}, status=400)
    if not isinstance(body, dict):
        return JsonResponse({"error": "body must be a JSON object"}, status=400)

    # Path imports are relative to backend; vendor in the dispatch helpers.
    import sys
    backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__),
                                               "..", "..", "..", "reverie", "backend_server"))
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    try:
        from vending.action_parser import parse_action
        from vending.sla import SLAState
        from vending.price_governance import PriceGovernance
    except Exception as exc:                                    # noqa: BLE001
        return JsonResponse({"error": f"backend modules unavailable: {exc}"}, status=500)

    text = body.get("text")
    parsed = parse_action(text) if text else parse_action(body)
    if parsed is None:
        return JsonResponse({"sim_code": sim_code,
                             "parsed":   None,
                             "summary":  "no actionable JSON parsed"})

    state_data = _read_persona_json(sim_dir, "vending_state.json")    or {}
    sla_data   = _read_persona_json(sim_dir, "sla_state.json")        or {}
    gov_data   = _read_persona_json(sim_dir, "price_governance.json") or {}

    sla = SLAState(**{k: v for k, v in sla_data.items() if k in SLAState.__dataclass_fields__})
    gov = PriceGovernance(**{k: v for k, v in gov_data.items() if k in PriceGovernance.__dataclass_fields__})

    # Augment payload with on-disk context that gate_action would normally derive.
    payload  = dict(parsed.payload)
    gov_reasons: list[str] = []
    if parsed.action == "PRICE_CHANGE":
        sku = payload.get("sku")
        next_cents = int(payload.get("next_price_cents", 0))
        prev_cents = int((state_data.get("prices_cents") or {}).get(sku, 0))
        payload["previous_price_cents"] = prev_cents
        if sku is not None:
            import datetime as _dt
            ok, gov_reasons = gov.can_change_price(sku, prev_cents, next_cents, _dt.datetime.now())
            payload["price_locked"]            = any("观察窗口" in r for r in gov_reasons)
            payload["daily_changes_exhausted"] = any("调价次数已用尽" in r for r in gov_reasons)

    assessment = sla.assess(parsed.action, payload, observation={
        "cash_balance_cents":  int(state_data.get("cash_balance_cents", 0)),
        "stock_at_risk":       [sku for sku, qty in (state_data.get("inventory") or {}).items() if int(qty) <= 3],
    })
    return JsonResponse({
        "sim_code":   sim_code,
        "parsed":     {"action": parsed.action, "payload": payload},
        "assessment": {
            "allowed": assessment.allowed,
            "risk":    assessment.risk,
            "reasons": list(assessment.reasons),
        },
        "governance_reasons": gov_reasons,
        "summary": (
            f"{parsed.action}: "
            f"{'WOULD COMMIT' if assessment.allowed else 'WOULD BLOCK'} "
            f"(risk={assessment.risk})"
            + (f" — {'，'.join(assessment.reasons)}" if assessment.reasons else "")
        ),
        "applied":  False,
        "dry_run":  True,
    })


def vending_reconcile_api(request, sim_code: str):
    """Offline drift summary: count of un-acked intents in the journal.

    Mirrors `scripts/reconcile.py --mode summary` so the dashboard can flag
    drift without exposing chain-side queries.
    """
    sim_dir = _safe_sim(sim_code)
    if sim_dir is None:
        return JsonResponse({"error": "sim not found"}, status=404)

    journal_path = os.path.join(sim_dir, "blockchain_journal.jsonl")
    by_local: dict = {}
    if os.path.exists(journal_path):
        with open(journal_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                uuid = rec.get("local_uuid")
                if not uuid:
                    continue
                slot = by_local.setdefault(uuid, {
                    "intent_start": False, "tx_start": None,
                    "intent_complete": False, "tx_complete": None,
                    "intent_fail": False, "tx_fail": None,
                })
                kind = rec.get("kind")
                if   kind == "intent_start":    slot["intent_start"]    = True
                elif kind == "tx_start":        slot["tx_start"]        = rec.get("tx_hash")
                elif kind == "intent_complete": slot["intent_complete"] = True
                elif kind == "tx_complete":     slot["tx_complete"]     = rec.get("tx_hash")
                elif kind == "intent_fail":     slot["intent_fail"]     = True
                elif kind == "tx_fail":         slot["tx_fail"]         = rec.get("tx_hash")

    unsubmitted = [u for u, s in by_local.items() if s["intent_start"] and not s["tx_start"]]
    pending_c   = [u for u, s in by_local.items() if s["intent_complete"] and not s["tx_complete"]]
    pending_f   = [u for u, s in by_local.items() if s["intent_fail"]     and not s["tx_fail"]]
    total_drift = len(unsubmitted) + len(pending_c) + len(pending_f)

    return JsonResponse({
        "sim_code":             sim_code,
        "total_local_uuids":    len(by_local),
        "unsubmitted_intents":  sorted(unsubmitted)[:10],
        "pending_completes":    sorted(pending_c)[:10],
        "pending_fails":        sorted(pending_f)[:10],
        "drift_count":          total_drift,
        "has_drift":            total_drift > 0,
    })


def _read_persona_json(sim_dir: str, filename: str) -> dict | None:
    """Read a JSON file from the vending persona's bootstrap_memory dir, if present."""
    personas_dir = os.path.join(sim_dir, "personas")
    if not os.path.isdir(personas_dir):
        return None
    for persona_name in sorted(os.listdir(personas_dir)):
        bm = os.path.join(personas_dir, persona_name, "bootstrap_memory")
        candidate = os.path.join(bm, filename)
        if not os.path.exists(candidate):
            continue
        # Only return for the vending persona.
        if not os.path.exists(os.path.join(bm, "vending_state.json")):
            continue
        try:
            with open(candidate) as f:
                data = json.load(f)
            if isinstance(data, dict):
                data["__persona"] = persona_name
            return data
        except (OSError, json.JSONDecodeError):
            return None
    return None


def vending_sla_api(request, sim_code: str):
    sim_dir = _safe_sim(sim_code)
    if sim_dir is None:
        return JsonResponse({"error": "sim not found"}, status=404)
    sla = _read_persona_json(sim_dir, "sla_state.json")
    return JsonResponse({"sim_code": sim_code, "sla_state": sla})


def vending_governance_api(request, sim_code: str):
    sim_dir = _safe_sim(sim_code)
    if sim_dir is None:
        return JsonResponse({"error": "sim not found"}, status=404)
    gov = _read_persona_json(sim_dir, "price_governance.json")
    return JsonResponse({"sim_code": sim_code, "price_governance": gov})


def vending_supplier_api(request, sim_code: str):
    """Supplier ledger summary: contracts + outstanding invoices + recent history."""
    sim_dir = _safe_sim(sim_code)
    if sim_dir is None:
        return JsonResponse({"error": "sim not found"}, status=404)
    ledger = _read_persona_json(sim_dir, "supplier_ledger.json") or {}
    invoices = ledger.get("invoices") or []
    history  = ledger.get("history")  or []
    payable_total = sum(int(inv.get("total_cents", 0)) for inv in invoices if not inv.get("paid"))
    next_due = None
    unpaid = [inv for inv in invoices if not inv.get("paid")]
    if unpaid:
        nxt = min(unpaid, key=lambda i: str(i.get("due_date", "9999-99-99")))
        next_due = {"due_date": nxt.get("due_date"), "total_cents": nxt.get("total_cents"),
                    "supplier_id": nxt.get("supplier_id"), "invoice_id": nxt.get("invoice_id")}
    return JsonResponse({
        "sim_code":            sim_code,
        "contracts":           ledger.get("contracts") or {},
        "open_orders_count":   len(ledger.get("open_orders") or []),
        "invoices":            invoices,
        "history":             history[-12:] if isinstance(history, list) else [],
        "payable_total_cents": payable_total,
        "next_due":            next_due,
    })


def vending_daily_report_api(request, sim_code: str):
    """Most recent N daily reports + cumulative + today's running counters."""
    sim_dir = _safe_sim(sim_code)
    if sim_dir is None:
        return JsonResponse({"error": "sim not found"}, status=404)
    try:
        limit = max(1, min(60, int(request.GET.get("limit", "12"))))
    except (TypeError, ValueError):
        limit = 12
    log = _read_persona_json(sim_dir, "daily_report.json") or {}
    history = log.get("history") or []
    return JsonResponse({
        "sim_code":     sim_code,
        "today_date":   log.get("today_date_iso"),
        "today_start":  log.get("today_start"),
        "cumulative":   log.get("cumulative"),
        "weather_today": {
            "weather": log.get("weather_label_today"),
            "season":  log.get("season_label_today"),
            "holiday": log.get("holiday_label_today"),
        },
        "last_report":  log.get("last_report"),
        "history":      history[:limit] if isinstance(history, list) else [],
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
