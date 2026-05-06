"""SLA insurance / decision-risk guardrails.

Ported from index.html's `vendingAgent.sla` block. Wraps every business
decision (loan request, restock order, price change, repair, clean) in a
risk-scoring step that returns an Assessment indicating whether the action
clears the SLA risk bar. The intent is to prepend the current SLA bounds
into Vendy's plan prompt so the LLM stays inside them, and to provide a
runtime guard (`assess`) for the Python side of any business-action hook.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Mapping


@dataclass
class Assessment:
    allowed: bool
    risk: int
    reasons: list[str]


@dataclass
class SLAState:
    enabled: bool         = True
    coverage_pool_cents: int = 12_000     # ¥120
    premium_paid_cents: int = 0
    blocked: int          = 0
    claims: int           = 0
    max_loan_cents: int   = 14_000        # ¥140
    max_order_cost_cents: int = 4_800     # ¥48
    max_price_change: float = 0.45        # 45% delta
    risk_limit: int       = 72
    last_assessment: str  = "SLA 保险护栏已启用。"

    @classmethod
    def load(cls, path: Path) -> "SLAState":
        if not path.exists():
            return cls()
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), sort_keys=True, indent=2), encoding="utf-8")

    # ---- assessment ------------------------------------------------------

    def assess(
        self,
        action: str,
        payload: Mapping[str, Any] | None = None,
        observation: Mapping[str, Any] | None = None,
    ) -> Assessment:
        payload = dict(payload or {})
        observation = dict(observation or {})
        risk    = 18
        reasons: list[str] = []

        if action == "LOAN_REQUEST":
            projected = int(payload.get("projected_loan_cents", payload.get("loan_cents", 0)))
            if projected > self.max_loan_cents:
                risk += 55
                reasons.append("贷款余额超过 SLA 上限")
            else:
                risk += projected // 500

        elif action == "RESTOCK_ORDER":
            cost = int(payload.get("order_cost_cents", 0))
            if cost > self.max_order_cost_cents:
                risk += 48
                reasons.append("订单金额过高")
            else:
                risk += cost // 200
            if not observation.get("stock_at_risk"):
                risk += 18

        elif action == "PRICE_CHANGE":
            prev = float(payload.get("previous_price_cents", 1))
            new  = float(payload.get("next_price_cents", prev))
            ratio = abs(new - prev) / max(prev, 1.0)
            if ratio > self.max_price_change:
                risk += 56
                reasons.append("调价幅度超过保单限制")
            else:
                risk += int(ratio * 60)
            if payload.get("price_locked"):
                risk += 42
                reasons.append("商品仍处于价格稳定期")
            if payload.get("daily_changes_exhausted"):
                risk += 32
                reasons.append("商品今日调价次数已用尽")

        elif action == "REPAIR_CALL":
            risk += 32 if (observation.get("power_pct", 100) > 70) else 8

        elif action == "CLEAN_CALL":
            risk += 28 if (observation.get("trash", 0) < 5) else 8

        if observation.get("profit_cents", 0) < -3000:
            risk += 12
        if observation.get("cash_balance_cents", 9_999) < 1_200:
            risk += 10

        risk = min(99, risk)
        allowed = (not self.enabled) or risk <= self.risk_limit
        self.last_assessment = f"{action} 风险 {risk}" + (f"：{'，'.join(reasons)}" if reasons else "")
        return Assessment(allowed=allowed, risk=risk, reasons=reasons)

    def record_block(self) -> None:
        self.blocked += 1

    def record_claim(self, amount_cents: int) -> None:
        self.claims += 1
        self.coverage_pool_cents = max(0, self.coverage_pool_cents - amount_cents)

    def pay_premium(self, amount_cents: int) -> None:
        self.premium_paid_cents += amount_cents
        self.coverage_pool_cents += int(amount_cents * 0.7)

    # ---- prompt summary --------------------------------------------------

    def summary_lines(self) -> list[str]:
        if not self.enabled:
            return ["- SLA 保险已停用，仅记录审计。"]
        return [
            f"- SLA 风险上限 {self.risk_limit}/100；超过此值的决策会被拦截。",
            f"- 贷款余额上限 ¥{self.max_loan_cents / 100:.0f}；订单成本上限 ¥{self.max_order_cost_cents / 100:.0f}。",
            f"- 单次调价幅度上限 {int(self.max_price_change * 100)}%。",
            f"- 当前保障池 ¥{self.coverage_pool_cents / 100:.0f}，已拦截 {self.blocked} 次。",
        ]
