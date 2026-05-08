"""Auto-loan workflow: bridge supplier-payment shortfalls through the SLA gate.

When the supplier ledger raises InsufficientFundsError on its 支払日, Vendy
faces a hard miss — the cleanest recovery is to take a short-term bank loan
sized to the shortfall (plus a small buffer), through the same SLA risk
gate that protects every other business decision.

  request_loan_for_shortfall(persona, shortfall_cents, now)
    -> LoanResult(granted_cents, assessment, summary)

If allowed:
  - persona.vending_state.cash_balance_cents      += granted
  - persona.vending_state.outstanding_loans_cents += granted
  - emits business_action_committed → TaskRegistry adapter records one
    ACTION row (description "贷款 ¥…，覆盖供货商欠款"), and DailyReportLog
    sees it via action_start (the audit counter).

If blocked:
  - granted == 0; outstanding loans untouched
  - business_action_committed is NOT emitted (chain stays clean of
    speculative attempts)
  - sla_blocked is emitted (already by gate_action), so DailyReportLog
    increments today's blocked count
  - the caller decides: re-raise, escalate, or defer the supplier payment

Buffer policy: we ask for shortfall + 10% (rounded up to ¥100) so a single
loan can absorb minor sale variance until the next restock without back-
to-back loan requests.
"""

from __future__ import annotations

import datetime as _dt
import logging
import math
from dataclasses import dataclass
from typing import Any

from .action_gate import gate_action
from .events import bus
from .sla import Assessment


log = logging.getLogger(__name__)

VENDY = "Vendy Unit-001"


@dataclass
class LoanResult:
    granted_cents: int
    assessment:    Assessment | None
    summary:       str


def _round_up_to_100_yen(amount_cents: int) -> int:
    """Round up to the nearest ¥100 (10000 cents per ¥100 — wait: 100 cents per ¥1).

    Loans are typically issued in ¥100 increments by Japanese banks, so we
    round up to that. (1 yen = 100 of our cents-of-yen.)
    """
    bucket = 10_000   # ¥100
    return int(math.ceil(amount_cents / bucket) * bucket)


def request_loan_for_shortfall(
    persona: Any,
    shortfall_cents: int,
    now: _dt.datetime,
    *,
    buffer_pct: float = 0.10,
    reason: str = "supplier settlement",
) -> LoanResult:
    if shortfall_cents <= 0:
        return LoanResult(0, None, "no shortfall, no loan needed")

    state = getattr(persona, "vending_state", None)
    if state is None:
        return LoanResult(0, None, "persona has no vending_state")

    desired = _round_up_to_100_yen(int(shortfall_cents * (1.0 + buffer_pct)))
    projected_total = state.outstanding_loans_cents + desired

    assessment = gate_action(
        persona, "LOAN_REQUEST",
        payload={
            "loan_cents":           desired,
            "projected_loan_cents": projected_total,
            "reason":               reason,
        },
        observation={
            "cash_balance_cents": state.cash_balance_cents,
            "shortfall_cents":    shortfall_cents,
        },
    )
    if not assessment.allowed:
        line = (
            f"LOAN_REQUEST ¥{desired / 100:.0f} ({reason}): BLOCKED "
            f"(risk={assessment.risk}/100；{'，'.join(assessment.reasons) or '无明确原因'})"
        )
        log.warning("auto-loan blocked: %s", line)
        return LoanResult(0, assessment, line)

    state.cash_balance_cents      += desired
    state.outstanding_loans_cents += desired

    bus.publish({
        "kind":        "business_action_committed",
        "persona":     VENDY,
        "action":      "LOAN_REQUEST",
        "description": (
            f"贷款 ¥{desired / 100:.0f}（{reason}），"
            f"覆盖缺口 ¥{shortfall_cents / 100:.0f}；"
            f"未偿余额 ¥{state.outstanding_loans_cents / 100:.0f}"
        ),
        "event_spo":   (VENDY, "borrowed", reason),
        "payload":     {"granted_cents":             desired,
                        "shortfall_cents":           shortfall_cents,
                        "projected_total_cents":     projected_total,
                        "reason":                    reason},
        "timestamp":   now.isoformat(),
    })
    line = (
        f"LOAN_REQUEST ¥{desired / 100:.0f} ({reason}): committed; "
        f"现金 ¥{state.cash_balance_cents / 100:.0f}, "
        f"债务 ¥{state.outstanding_loans_cents / 100:.0f}"
    )
    return LoanResult(desired, assessment, line)
