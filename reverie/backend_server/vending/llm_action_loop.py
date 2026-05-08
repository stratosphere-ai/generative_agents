"""Hourly LLM action loop.

Once per hour Vendy can be asked: "Given the current preamble (state, SLA,
governance, environment, supplier, yesterday's report), what business
action — if any — should you take this hour?" The LLM is expected to
answer with a single JSON object matching the action_parser schema.

This module is deliberately minimal:

    request_business_action(persona, now=None) -> ActionResult

Internals:
  1. Build the prompt using prompt_inject.state_preamble — same context
     Vendy already sees in plan generation. Adds explicit instructions
     for the JSON schema and a hard "if nothing is needed, return NO_OP"
     escape hatch.
  2. Route through model_router.with_persona so customers stay on the
     cheap model and Vendy stays on the main one.
  3. Send via openai.ChatCompletion.create. Failures (timeout, parse error)
     return ActionResult(applied=False, summary="error: ...") rather than
     raise — the sim should never crash because of a flaky LLM.
  4. Parse + dispatch via action_parser.dispatch.
  5. Append `summary` to persona.memory if available, so the next plan
     prompt sees the audit trail.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
from dataclasses import dataclass
from typing import Any, Optional

from .action_parser import dispatch
from .model_router import with_persona
from .prompt_inject import state_preamble


log = logging.getLogger(__name__)


PROMPT_INSTRUCTIONS = """
You are Vendy Unit-001, an autonomous vending machine. Below is the live
context for this hour: your stock, prices, cash, SLA guardrails, price
governance constraints, today's environment, supplier obligations, and
yesterday's report.

Decide whether to take ONE business action this hour, then output a single
JSON object — nothing else. Allowed shapes:

  {"action": "PRICE_CHANGE",  "sku": <SKU>, "next_price_cents": <int>,
   "rationale": <str>}

  {"action": "RESTOCK_ORDER", "supplier_id": <str>,
   "items": [[<SKU>, <qty>], ...],
   "rationale": <str>}

  {"action": "NO_OP", "rationale": <str>}

Hard rules:
  - Stay inside the SLA risk_limit and price-governance step / cooldown.
  - Do NOT invent SKUs or supplier_ids that aren't in the preamble.
  - Output exactly ONE JSON object. No prose before or after.
""".strip()


@dataclass
class ActionResult:
    raw_text:    str
    applied:     bool
    summary:     str
    parsed_action: Optional[str] = None


def _build_prompt(persona: Any) -> str:
    preamble = state_preamble(persona) or "(no business state attached)"
    return f"{preamble}\n\n{PROMPT_INSTRUCTIONS}"


def _llm_complete(prompt: str, *, timeout_sec: float = 30.0) -> str:
    """Single-shot ChatCompletion. Returns "" on any failure."""
    try:
        import openai                                       # type: ignore[import-not-found]
    except ImportError:
        return ""
    try:
        resp = openai.ChatCompletion.create(
            model="gpt-4o",                                  # model_router rewrites this
            messages=[{"role": "user", "content": prompt}],
            request_timeout=timeout_sec,
        )
        return resp["choices"][0]["message"]["content"] or ""
    except Exception as exc:                                 # noqa: BLE001
        log.warning("llm action loop: openai call failed: %s", exc)
        return ""


def _remember(persona: Any, line: str) -> None:
    mem = getattr(persona, "memory", None)
    if isinstance(mem, list):
        mem.insert(0, line)
        del mem[8:]


def request_business_action(
    persona: Any,
    *,
    now: _dt.datetime | None = None,
) -> ActionResult:
    """Ask the LLM for one action this hour and run it through the gate."""
    now = now or _dt.datetime.now()
    prompt = _build_prompt(persona)
    with with_persona(persona):
        text = _llm_complete(prompt)
    if not text:
        return ActionResult(raw_text="", applied=False, summary="error: empty LLM response")

    assess, applied, summary = dispatch(persona, text, now=now)
    parsed_action = None
    if assess is not None:
        # action name is encoded in the summary's first token in our format
        parsed_action = summary.split(" ", 1)[0]

    _remember(persona, f"[{now.strftime('%m-%d %H:%M')}] {summary}")
    return ActionResult(raw_text=text, applied=applied, summary=summary,
                        parsed_action=parsed_action)
