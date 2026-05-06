"""Daily KPI snapshot + reflection thought generator.

Ports `generateDailyReport` from index.html (L2087-2117) into the Python
backend. Tracks per-day sales / revenue / cost / missed events; at midnight
a `DailyReport` is synthesized and added to the persona as a fresh
"reflection thought" so the next plan prompt sees yesterday's outcome.

Two integration points:

    1. record_*() — called from vending.transaction.sell + bus listeners
       throughout the day to accumulate counters.
    2. close_day(date, ...) — called once at midnight (reverie cross-day
       hook). Produces a DailyReport; the caller appends its `thought_text`
       to the persona's associative memory and pins `last_report` on the
       persona for prompt_inject to surface.
"""

from __future__ import annotations

import datetime as _dt
import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


@dataclass
class DailySnapshot:
    """Cumulative KPIs at the start (or end) of a day."""
    date_iso:           str
    revenue_cents:      int = 0
    cost_cents:         int = 0
    sales_count:        int = 0
    missed_count:       int = 0
    cash_balance_cents: int = 0
    payable_cents:      int = 0
    audit_count:        int = 0
    sla_blocked:        int = 0


@dataclass
class DailyReport:
    """One day's deltas + a natural-language thought text."""
    date_iso:           str
    sales_count:        int
    revenue_cents:      int
    cost_cents:         int
    profit_cents:       int
    missed_count:       int
    weather_label:      str
    season_label:       str
    holiday_label:      str
    avg_traffic:        float
    peak_traffic:       float
    sla_blocked:        int
    audit_count:        int
    payable_delta_cents: int
    next_due_date_iso:  str | None
    thought_text:       str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DailyReportLog:
    """Accumulator + day-rollover engine."""
    today_date_iso:  str | None         = None
    today_start:     DailySnapshot | None = None
    cumulative:      DailySnapshot      = field(default_factory=lambda: DailySnapshot(date_iso=""))
    weather_samples: list[float]        = field(default_factory=list)
    weather_label_today: str            = "—"
    season_label_today:  str            = "—"
    holiday_label_today: str            = "—"
    last_report:     DailyReport | None = None
    history:         list[DailyReport]  = field(default_factory=list)
    history_cap:     int                = 30

    # ---- daily counters --------------------------------------------------

    def record_sale(self, *, price_cents: int, cost_cents: int) -> None:
        self.cumulative.revenue_cents += price_cents
        self.cumulative.cost_cents    += cost_cents
        self.cumulative.sales_count   += 1

    def record_missed(self) -> None:
        self.cumulative.missed_count += 1

    def record_block(self) -> None:
        self.cumulative.sla_blocked += 1

    def record_audit(self) -> None:
        self.cumulative.audit_count += 1

    def record_traffic_sample(self, traffic_index: float) -> None:
        self.weather_samples.append(traffic_index)

    def record_environment(self, *, weather_label: str, season_label: str, holiday_label: str) -> None:
        self.weather_label_today = weather_label
        self.season_label_today  = season_label
        self.holiday_label_today = holiday_label

    # ---- day rollover ----------------------------------------------------

    def begin_day(self, date_iso: str, *, cash_balance_cents: int, payable_cents: int) -> None:
        if self.today_date_iso == date_iso:
            return
        self.today_date_iso = date_iso
        self.today_start = DailySnapshot(
            date_iso           = date_iso,
            revenue_cents      = self.cumulative.revenue_cents,
            cost_cents         = self.cumulative.cost_cents,
            sales_count        = self.cumulative.sales_count,
            missed_count       = self.cumulative.missed_count,
            cash_balance_cents = cash_balance_cents,
            payable_cents      = payable_cents,
            audit_count        = self.cumulative.audit_count,
            sla_blocked        = self.cumulative.sla_blocked,
        )
        self.weather_samples = []

    def close_day(
        self,
        *,
        cash_balance_cents: int,
        payable_cents: int,
        next_due_date: _dt.date | None = None,
    ) -> DailyReport:
        if self.today_start is None or self.today_date_iso is None:
            raise RuntimeError("close_day called before begin_day")

        sales   = self.cumulative.sales_count - self.today_start.sales_count
        revenue = self.cumulative.revenue_cents - self.today_start.revenue_cents
        cost    = self.cumulative.cost_cents - self.today_start.cost_cents
        profit  = revenue - cost
        missed  = self.cumulative.missed_count - self.today_start.missed_count
        blocked = self.cumulative.sla_blocked - self.today_start.sla_blocked
        audits  = self.cumulative.audit_count - self.today_start.audit_count
        payable_delta = payable_cents - self.today_start.payable_cents

        avg = sum(self.weather_samples) / len(self.weather_samples) if self.weather_samples else 0.0
        peak = max(self.weather_samples) if self.weather_samples else 0.0

        thought = (
            f"昨日（{self.today_date_iso}・{self.holiday_label_today}・{self.weather_label_today}/"
            f"{self.season_label_today}）销售 {sales} 单，"
            f"收入 ¥{revenue / 100:.0f}，成本 ¥{cost / 100:.0f}，"
            f"利润 ¥{profit / 100:.0f}；流失 {missed}，SLA 拦截 {blocked}。"
            f"平均客流 {avg:.2f}，峰值 {peak:.2f}。"
        )

        report = DailyReport(
            date_iso            = self.today_date_iso,
            sales_count         = sales,
            revenue_cents       = revenue,
            cost_cents          = cost,
            profit_cents        = profit,
            missed_count        = missed,
            weather_label       = self.weather_label_today,
            season_label        = self.season_label_today,
            holiday_label       = self.holiday_label_today,
            avg_traffic         = avg,
            peak_traffic        = peak,
            sla_blocked         = blocked,
            audit_count         = audits,
            payable_delta_cents = payable_delta,
            next_due_date_iso   = next_due_date.isoformat() if next_due_date else None,
            thought_text        = thought,
        )
        self.last_report = report
        self.history.insert(0, report)
        del self.history[self.history_cap:]
        self.today_start = None
        self.today_date_iso = None
        return report

    # ---- prompt ---------------------------------------------------------

    def summary_lines(self) -> list[str]:
        if self.last_report is None:
            return ["- 昨日尚无总结（首日运营）。"]
        r = self.last_report
        lines = [
            f"- 昨日（{r.date_iso}・{r.weather_label}/{r.season_label}・{r.holiday_label}）销售 {r.sales_count} 单。",
            f"- 收入 ¥{r.revenue_cents / 100:.0f}，成本 ¥{r.cost_cents / 100:.0f}，利润 ¥{r.profit_cents / 100:.0f}。",
            f"- 流失 {r.missed_count}，SLA 拦截 {r.sla_blocked}，审计 {r.audit_count} 条。",
            f"- 客流均值 {r.avg_traffic:.2f}，峰值 {r.peak_traffic:.2f}。",
        ]
        if r.next_due_date_iso:
            lines.append(f"- 次回支払日：{r.next_due_date_iso}（差额 ¥{r.payable_delta_cents / 100:+.0f}）。")
        return lines

    # ---- persistence ----------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "today_date_iso":      self.today_date_iso,
            "today_start":         asdict(self.today_start) if self.today_start else None,
            "cumulative":          asdict(self.cumulative),
            "weather_samples":     list(self.weather_samples),
            "weather_label_today": self.weather_label_today,
            "season_label_today":  self.season_label_today,
            "holiday_label_today": self.holiday_label_today,
            "last_report":         self.last_report.to_dict() if self.last_report else None,
            "history":             [r.to_dict() for r in self.history],
            "history_cap":         self.history_cap,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DailyReportLog":
        log = cls(history_cap=int(data.get("history_cap", 30)))
        log.today_date_iso = data.get("today_date_iso")
        if data.get("today_start"):
            log.today_start = DailySnapshot(**data["today_start"])
        if data.get("cumulative"):
            log.cumulative = DailySnapshot(**data["cumulative"])
        log.weather_samples = list(data.get("weather_samples", []))
        log.weather_label_today = data.get("weather_label_today", "—")
        log.season_label_today  = data.get("season_label_today", "—")
        log.holiday_label_today = data.get("holiday_label_today", "—")
        if data.get("last_report"):
            log.last_report = DailyReport(**data["last_report"])
        for r in data.get("history", []):
            log.history.append(DailyReport(**r))
        return log

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), default=str, sort_keys=True, indent=2))

    @classmethod
    def load(cls, path: Path) -> "DailyReportLog":
        if not path.exists():
            return cls()
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))
