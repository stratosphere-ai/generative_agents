"""Vendy's pricing governance: rate limits + change windows.

Ported from index.html's `economy.priceGovernance`. Holds the policy
constants, tracks per-SKU last-change timestamps and per-day change counts,
and exposes a `can_change_price` predicate the LLM/Python side can consult
before committing a price update.
"""

from __future__ import annotations

import datetime as _dt
import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


@dataclass
class PriceGovernance:
    min_interval_hours: int       = 12      # cooldown between two changes for one SKU
    max_daily_changes: int        = 1       # per-SKU daily cap
    max_single_change_rate: float = 0.12    # 12% per change

    last_changed: dict[str, str]  = field(default_factory=dict)   # sku -> ISO datetime
    changes_today: dict[str, int] = field(default_factory=dict)   # sku -> int
    today_iso_date: str | None    = None
    last_action: str              = "价格处于稳定观察窗口。"

    @classmethod
    def load(cls, path: Path) -> "PriceGovernance":
        if not path.exists():
            return cls()
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), sort_keys=True, indent=2), encoding="utf-8")

    # ---- daily roll ------------------------------------------------------

    def _maybe_roll_day(self, now: _dt.datetime) -> None:
        date_str = now.date().isoformat()
        if self.today_iso_date != date_str:
            self.today_iso_date = date_str
            self.changes_today = {}
            self.last_action = "新营业日开始，调价次数已清零。"

    # ---- predicate -------------------------------------------------------

    def can_change_price(
        self,
        sku: str,
        previous_price_cents: int,
        next_price_cents: int,
        now: _dt.datetime,
    ) -> tuple[bool, list[str]]:
        self._maybe_roll_day(now)
        reasons: list[str] = []

        last_iso = self.last_changed.get(sku)
        if last_iso:
            last_dt = _dt.datetime.fromisoformat(last_iso)
            hours = (now - last_dt).total_seconds() / 3600.0
            if hours < self.min_interval_hours:
                remaining = self.min_interval_hours - hours
                reasons.append(
                    f"{sku} 仍处于稳定观察窗口（剩余 {remaining:.1f}h）"
                )

        used = self.changes_today.get(sku, 0)
        if used >= self.max_daily_changes:
            reasons.append(f"{sku} 今日调价次数已用尽（{used}/{self.max_daily_changes}）")

        if previous_price_cents > 0:
            ratio = abs(next_price_cents - previous_price_cents) / previous_price_cents
            if ratio > self.max_single_change_rate:
                reasons.append(
                    f"{sku} 调价幅度 {ratio * 100:.1f}% 超过 {int(self.max_single_change_rate * 100)}% 限制"
                )

        return (not reasons), reasons

    # ---- mutation --------------------------------------------------------

    def record_change(
        self,
        sku: str,
        previous_price_cents: int,
        next_price_cents: int,
        now: _dt.datetime,
    ) -> None:
        self._maybe_roll_day(now)
        self.last_changed[sku] = now.isoformat()
        self.changes_today[sku] = self.changes_today.get(sku, 0) + 1
        delta_pct = 0.0
        if previous_price_cents:
            delta_pct = (next_price_cents - previous_price_cents) / previous_price_cents * 100
        self.last_action = (
            f"{sku} 价格 ¥{previous_price_cents/100:.2f} → ¥{next_price_cents/100:.2f}"
            f"（{delta_pct:+.1f}%）"
        )

    # ---- prompt summary --------------------------------------------------

    def summary_lines(self) -> list[str]:
        return [
            f"- 同一 SKU 最少间隔 {self.min_interval_hours}h、每日最多 {self.max_daily_changes} 次、单次最大 {int(self.max_single_change_rate * 100)}%。",
            f"- {self.last_action}",
        ]
