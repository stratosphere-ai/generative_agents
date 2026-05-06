"""Subscribe to the vending event bus and route into Vendy's DailyReportLog.

Mirrors the pattern of `registry_adapter.TaskRegistryAdapter`:
the listener holds a reference to one persona's `daily_report_log` and
routes bus events into the matching `record_*` / `begin_day` / `close_day`
calls. Cost lookups consult the persona's `supplier_ledger` SKU unit costs
(falling back to half the sale price when no contract specifies one).
"""

from __future__ import annotations

import datetime as _dt
import logging
from typing import Any, Mapping

from .events import bus


log = logging.getLogger(__name__)

VENDY = "Vendy Unit-001"


class DailyReportListener:
    def __init__(self, persona: Any) -> None:
        self.persona = persona

    # ---- subscription ----------------------------------------------------

    def attach(self) -> None:
        bus.subscribe(self._on_event)

    def detach(self) -> None:
        bus.unsubscribe(self._on_event)

    # ---- helpers ---------------------------------------------------------

    @property
    def _log(self):
        return getattr(self.persona, "daily_report_log", None)

    def _cost_for(self, sku: str, fallback_price_cents: int) -> int:
        ledger = getattr(self.persona, "supplier_ledger", None)
        if ledger is not None:
            for contract in ledger.contracts.values():
                cost = contract.sku_unit_costs_cents.get(sku)
                if cost:
                    return int(cost)
        return fallback_price_cents // 2  # rough COGS estimate when no contract

    def _vendy_state(self):
        return getattr(self.persona, "vending_state", None)

    def _payable_total_cents(self) -> int:
        ledger = getattr(self.persona, "supplier_ledger", None)
        return ledger.payable_total_cents() if ledger else 0

    def _next_due(self):
        ledger = getattr(self.persona, "supplier_ledger", None)
        return ledger.next_due() if ledger else None

    # ---- dispatch --------------------------------------------------------

    def _on_event(self, event: Mapping[str, Any]) -> None:
        log_obj = self._log
        if log_obj is None:
            return
        kind = event.get("kind")
        try:
            handler = getattr(self, f"_on_{kind}", None)
            if handler is not None:
                handler(event)
        except Exception:                                  # noqa: BLE001
            log.exception("daily report listener failed for event %r", kind)

    # ---- handlers --------------------------------------------------------

    def _on_day_start(self, e: Mapping[str, Any]) -> None:
        if e.get("persona") != VENDY:
            return
        vs = self._vendy_state()
        cash = vs.cash_balance_cents if vs else 0
        self._log.begin_day(
            e["date"],
            cash_balance_cents=cash,
            payable_cents=self._payable_total_cents(),
        )

    def _on_day_end(self, e: Mapping[str, Any]) -> None:
        if e.get("persona") != VENDY:
            return
        vs = self._vendy_state()
        cash = vs.cash_balance_cents if vs else 0
        nxt = self._next_due()
        try:
            self._log.close_day(
                cash_balance_cents=cash,
                payable_cents=self._payable_total_cents(),
                next_due_date=nxt.due_date if nxt else None,
            )
        except RuntimeError:
            # close_day called without a matching begin_day — first launch on
            # an existing sim. Skip silently.
            pass

    def _on_transaction_completed(self, e: Mapping[str, Any]) -> None:
        if e.get("persona") != VENDY:
            return
        price = int(e.get("price_cents", 0))
        cost  = self._cost_for(e.get("sku", ""), price)
        self._log.record_sale(price_cents=price, cost_cents=cost)

    def _on_transaction_missed(self, e: Mapping[str, Any]) -> None:
        if e.get("persona") != VENDY:
            return
        self._log.record_missed()

    def _on_action_start(self, e: Mapping[str, Any]) -> None:
        if e.get("persona") != VENDY:
            return
        self._log.record_audit()

    def _on_sla_blocked(self, e: Mapping[str, Any]) -> None:
        if e.get("persona") != VENDY:
            return
        self._log.record_block()

    def _on_env_tick(self, e: Mapping[str, Any]) -> None:
        if e.get("persona") != VENDY:
            return
        traffic = float(e.get("traffic_index", 0.0))
        self._log.record_traffic_sample(traffic)
        weather = e.get("weather_label")
        season  = e.get("season_label")
        holiday = e.get("holiday_label")
        if weather and season and holiday:
            self._log.record_environment(
                weather_label=weather,
                season_label=season,
                holiday_label=holiday,
            )
