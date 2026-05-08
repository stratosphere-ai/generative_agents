"""Parse LLM output into a structured business action and dispatch it.

Recommended LLM output schema (single JSON object, optionally inside a code
block, optionally surrounded by free-text reasoning):

    {
      "action":               "PRICE_CHANGE" | "RESTOCK_ORDER" | "NO_OP",

      // PRICE_CHANGE fields:
      "sku":                  "Coke",
      "next_price_cents":     158,            // or "next_price_yen": 1.58

      // RESTOCK_ORDER fields:
      "supplier_id":          "suntory",
      "items":                [["Coke", 12], ["Water", 12]],

      // optional, ignored except for logging:
      "rationale":            "lunch peak coming, Coke under 5 units"
    }

`parse_action(text)` is tolerant:
    - JSON in a ```json fenced block
    - JSON inline anywhere in the text (first `{` that successfully parses)
    - case-insensitive action names
    - prices accepted as `next_price_cents` (int) or `next_price_yen` (float)

`dispatch(persona, text, now)` parses, routes through the SLA action gate,
and returns (Assessment, applied: bool, summary: str). The caller (the
plan-loop hook or a test harness) decides what to do with the assessment;
nothing in here mutates without a successful gate.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Mapping

from .action_gate import commit_price_change, commit_restock
from .sla import Assessment


log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


@dataclass
class ParsedAction:
    action:  str
    payload: dict[str, Any]


_FENCED_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)


def _extract_json_blob(text: str) -> str | None:
    if not text:
        return None
    fenced = _FENCED_RE.search(text)
    if fenced:
        return fenced.group(1)

    # Walk every `{` and try to parse the substring up to a matching `}`.
    for start in (i for i, c in enumerate(text) if c == "{"):
        depth = 0
        for end in range(start, len(text)):
            if text[end] == "{":
                depth += 1
            elif text[end] == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start:end + 1]
                    try:
                        json.loads(candidate)
                        return candidate
                    except json.JSONDecodeError:
                        break
    return None


def parse_action(text: str | Mapping[str, Any]) -> ParsedAction | None:
    """Return a normalized ParsedAction or None if no recognizable action."""
    if isinstance(text, Mapping):
        data: dict[str, Any] = dict(text)
    else:
        blob = _extract_json_blob(text or "")
        if not blob:
            return None
        try:
            data = json.loads(blob)
        except json.JSONDecodeError:
            return None
    if not isinstance(data, dict):
        return None

    action = str(data.get("action", "")).strip().upper()
    if action == "NO_OP" or action == "":
        return None

    payload: dict[str, Any] = {k: v for k, v in data.items() if k != "action"}

    # Normalize prices: accept yen (float) → cents.
    if action == "PRICE_CHANGE":
        if "next_price_cents" not in payload and "next_price_yen" in payload:
            try:
                payload["next_price_cents"] = int(round(float(payload["next_price_yen"]) * 100))
            except (TypeError, ValueError):
                return None

    # Normalize items: accept list of {"sku", "qty"} dicts too.
    if action == "RESTOCK_ORDER":
        items = payload.get("items")
        if isinstance(items, list):
            norm: list[tuple[str, int]] = []
            for it in items:
                if isinstance(it, (list, tuple)) and len(it) == 2:
                    norm.append((str(it[0]), int(it[1])))
                elif isinstance(it, dict) and "sku" in it and "qty" in it:
                    norm.append((str(it["sku"]), int(it["qty"])))
                else:
                    return None
            payload["items"] = norm

    return ParsedAction(action=action, payload=payload)


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def dispatch(
    persona: Any,
    text: str | Mapping[str, Any],
    now: _dt.datetime | None = None,
) -> tuple[Assessment | None, bool, str]:
    """Parse text and run the resulting action through the SLA gate.

    Returns (assessment, applied, summary):
      - assessment is None if parsing produced nothing
      - applied is True iff the action passed the gate AND mutated state
      - summary is a human-readable status line for prompt-side logging
    """
    parsed = parse_action(text)
    if parsed is None:
        return None, False, "no actionable JSON parsed"

    now = now or _dt.datetime.now()
    rationale = str(parsed.payload.get("rationale", "")).strip()

    if parsed.action == "PRICE_CHANGE":
        sku = parsed.payload.get("sku")
        new_cents = parsed.payload.get("next_price_cents")
        if not sku or not isinstance(new_cents, (int, float)):
            return None, False, f"PRICE_CHANGE missing sku/next_price_cents (rationale={rationale!r})"
        assessment = commit_price_change(
            persona, sku=str(sku), new_price_cents=int(new_cents), now=now,
        )
        applied = assessment.allowed
        line = f"PRICE_CHANGE {sku}→¥{int(new_cents) / 100:.2f}: {'committed' if applied else 'BLOCKED'}"
        if assessment.reasons:
            line += f"（{'，'.join(assessment.reasons)}）"
        return assessment, applied, line

    if parsed.action == "RESTOCK_ORDER":
        supplier_id = parsed.payload.get("supplier_id")
        items       = parsed.payload.get("items")
        if not supplier_id or not items:
            return None, False, f"RESTOCK_ORDER missing supplier_id/items (rationale={rationale!r})"
        try:
            assessment = commit_restock(
                persona,
                supplier_id=str(supplier_id),
                items=list(items),
                order_date=now.date(),
                observation=parsed.payload.get("observation") or {},
            )
        except RuntimeError as exc:
            return None, False, f"RESTOCK_ORDER {supplier_id}: rejected ({exc})"
        applied = assessment.allowed
        items_desc = "、".join(f"{s}×{q}" for s, q in items)
        line = f"RESTOCK_ORDER {supplier_id} {items_desc}: {'committed' if applied else 'BLOCKED'}"
        if assessment.reasons:
            line += f"（{'，'.join(assessment.reasons)}）"
        return assessment, applied, line

    return None, False, f"unknown action {parsed.action!r}"
