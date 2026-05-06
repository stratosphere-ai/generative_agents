"""Prefix VendingState facts onto Vendy's plan/reflect prompts.

We do NOT touch any prompt template. Instead, the prompt code reads the
persona's daily-plan requirement via `persona.scratch.get_str_daily_plan_req()`;
we wrap that getter on the vending persona so it returns the stored
daily_plan_req plus a freshly-rendered state preamble. The wrapper lives
entirely on the bound method and does not modify on-disk scratch.json.
"""

from __future__ import annotations

from typing import Any


def state_preamble(persona: Any) -> str:
    state = getattr(persona, "vending_state", None)
    if state is None:
        return ""
    lines = [
        "## Business state (you, the vending machine)",
        f"- {state.stock_summary()}",
        f"- {state.price_summary()}",
        f"- cash on hand: ${state.cash_balance_cents / 100:.2f}",
        f"- power: {state.power_pct:.0f}%",
    ]
    if state.last_restock_iso:
        lines.append(f"- last restock: {state.last_restock_iso}")

    sla = getattr(persona, "sla_state", None)
    if sla is not None:
        lines.append("## SLA guardrails")
        lines.extend(sla.summary_lines())

    gov = getattr(persona, "price_governance", None)
    if gov is not None:
        lines.append("## Price governance")
        lines.extend(gov.summary_lines())

    env = getattr(persona, "env_snapshot", None)
    if env is not None:
        lines.append("## Environment")
        lines.extend(env.summary_lines())

    ledger = getattr(persona, "supplier_ledger", None)
    if ledger is not None:
        lines.append("## Supplier ledger (Japanese commercial cycle)")
        lines.extend(ledger.summary_lines())

    report = getattr(persona, "daily_report_log", None)
    if report is not None:
        lines.append("## Yesterday's daily report")
        lines.extend(report.summary_lines())

    return "\n".join(lines)


def attach_to_persona(persona: Any) -> None:
    """Wrap `persona.scratch.get_str_daily_plan_req` to append the live state preamble."""
    state = getattr(persona, "vending_state", None)
    if state is None:
        return

    scratch = persona.scratch
    if getattr(scratch, "_vending_inject_attached", False):
        return

    original = scratch.get_str_daily_plan_req

    def patched() -> str:
        body = original()
        preamble = state_preamble(persona)
        if not preamble:
            return body
        if not body:
            return preamble
        return f"{body}\n\n{preamble}"

    scratch.get_str_daily_plan_req = patched
    scratch._vending_inject_attached = True
