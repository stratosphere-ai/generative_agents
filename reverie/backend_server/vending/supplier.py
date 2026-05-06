"""Japanese-style supplier settlement (掛取引 / 締め支払い).

Models the commercial-credit cycle Vendy actually faces in Japan:

    1. 発注 (place_order)            — Vendy issues purchase orders to the
                                       supplier; goods ship on delivery_day.
    2. 締め日 (cutoff_day)           — At the contract's monthly cutoff, all
                                       open orders for that supplier collapse
                                       into one PendingInvoice. Consumption
                                       tax (8% reduced rate for food/drinks,
                                       軽減税率) is applied here.
    3. 支払日 (payment_day)          — Vendy is debited; the bank transfer fee
                                       (振込手数料) is added on Vendy's side
                                       when the contract puts it on the buyer.

Three default contract templates ship in the module:

    - matsujime_yokugetsu_matsu     末締め翌月末払い (most common, ~60d effective)
    - hatsuka_jime_yokugetsu_tooka  20日締め翌月10日払い
    - sokukin                       即金 (cash-on-delivery, 0d)

Anything more exotic (手形 60d/90d/120d, 検収締め, 早割) is a parameter tweak
on `SupplierContract` rather than a new class.
"""

from __future__ import annotations

import calendar
import datetime as _dt
import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Iterable


# Standard rates (as of 2024–2026; bump if tax law changes).
TAX_RATE_FOOD_DRINK_REDUCED = 0.08      # 軽減税率: 飲食料品
TAX_RATE_STANDARD           = 0.10      # 標準税率


class InsufficientFundsError(Exception):
    """Vendy's cash balance can't cover an invoice on its 支払日."""

    def __init__(self, invoice_id: str, shortfall_cents: int) -> None:
        super().__init__(f"invoice {invoice_id}: shortfall ¥{shortfall_cents / 100:.2f}")
        self.invoice_id = invoice_id
        self.shortfall_cents = shortfall_cents


# ---------------------------------------------------------------------------
# Contract definition
# ---------------------------------------------------------------------------

@dataclass
class SupplierContract:
    """One supplier's commercial terms.

    Attributes match Japanese 取引条件 vocabulary:

        cutoff_day                 締め日 (1-31; 31 means 月末)
        payment_offset_months      翌月=1, 翌々月=2, 当月=0
        payment_day                支払日 (1-31; 31 means 月末)
        consumption_tax_rate       消費税率 (0.08 reduced / 0.10 standard)
        transfer_fee_cents_buyer   買い手負担の振込手数料 (typically ¥220-880)
        early_payment_discount     早割 (0.0 - 0.05 typical)
    """
    supplier_id:               str
    label:                     str
    cutoff_day:                int  = 31
    payment_offset_months:     int  = 1
    payment_day:               int  = 31
    consumption_tax_rate:      float = TAX_RATE_FOOD_DRINK_REDUCED
    transfer_fee_cents_buyer:  int  = 220     # ¥220 同行/¥440 他行 are typical
    early_payment_discount:    float = 0.0
    sku_unit_costs_cents:      dict[str, int] = field(default_factory=dict)


def matsujime_yokugetsu_matsu(supplier_id: str, label: str, **kw) -> SupplierContract:
    """末締め翌月末払い — the canonical Japanese B2B credit term."""
    return SupplierContract(supplier_id=supplier_id, label=label,
                            cutoff_day=31, payment_offset_months=1, payment_day=31, **kw)


def hatsuka_jime_yokugetsu_tooka(supplier_id: str, label: str, **kw) -> SupplierContract:
    """20日締め翌月10日払い — short-cycle term, popular with food vendors."""
    return SupplierContract(supplier_id=supplier_id, label=label,
                            cutoff_day=20, payment_offset_months=1, payment_day=10, **kw)


def sokukin(supplier_id: str, label: str, **kw) -> SupplierContract:
    """即金 — cash on delivery, no AP, no transfer fee."""
    return SupplierContract(supplier_id=supplier_id, label=label,
                            cutoff_day=31, payment_offset_months=0, payment_day=31,
                            transfer_fee_cents_buyer=0, **kw)


# ---------------------------------------------------------------------------
# Orders + invoices
# ---------------------------------------------------------------------------

@dataclass
class PurchaseOrder:
    order_id:    str
    supplier_id: str
    order_date:  _dt.date
    items:       list[tuple[str, int]]    # (sku, quantity)
    invoiced:    bool = False


@dataclass
class InvoiceLine:
    sku:              str
    quantity:         int
    unit_cost_cents:  int

    @property
    def line_total_cents(self) -> int:
        return self.unit_cost_cents * self.quantity


@dataclass
class PendingInvoice:
    invoice_id:        str
    supplier_id:       str
    cutoff_date:       _dt.date
    due_date:          _dt.date
    lines:             list[InvoiceLine]
    subtotal_cents:    int
    tax_cents:         int
    transfer_fee_cents: int
    total_cents:       int
    paid:              bool          = False
    paid_date:         _dt.date | None = None

    def summary(self) -> str:
        return (
            f"{self.invoice_id}（締め {self.cutoff_date}, 支払 {self.due_date}）"
            f" 計 ¥{self.total_cents / 100:.0f}"
            f"（うち税 ¥{self.tax_cents / 100:.0f}）"
        )


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def _clamp_day(year: int, month: int, day: int) -> int:
    last = calendar.monthrange(year, month)[1]
    return min(day, last)


def cutoff_date_in_month(year: int, month: int, contract: SupplierContract) -> _dt.date:
    return _dt.date(year, month, _clamp_day(year, month, contract.cutoff_day))


def payment_date_for_cutoff(cutoff_date: _dt.date, contract: SupplierContract) -> _dt.date:
    y = cutoff_date.year
    m = cutoff_date.month + contract.payment_offset_months
    while m > 12:
        y, m = y + 1, m - 12
    return _dt.date(y, m, _clamp_day(y, m, contract.payment_day))


def is_cutoff_day(today: _dt.date, contract: SupplierContract) -> bool:
    return today == cutoff_date_in_month(today.year, today.month, contract)


def is_payment_day(today: _dt.date, contract: SupplierContract, invoice: PendingInvoice) -> bool:
    return today == invoice.due_date


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------

@dataclass
class SupplierLedger:
    contracts: dict[str, SupplierContract] = field(default_factory=dict)
    open_orders: list[PurchaseOrder]       = field(default_factory=list)
    invoices:    list[PendingInvoice]      = field(default_factory=list)   # cut, awaiting payment
    history:     list[PendingInvoice]      = field(default_factory=list)   # paid
    _seq:        int                       = 0

    # ---- contract management --------------------------------------------

    def add_contract(self, contract: SupplierContract) -> None:
        self.contracts[contract.supplier_id] = contract

    # ---- ordering -------------------------------------------------------

    def place_order(
        self,
        supplier_id: str,
        items: Iterable[tuple[str, int]],
        order_date: _dt.date,
    ) -> PurchaseOrder:
        if supplier_id not in self.contracts:
            raise KeyError(f"unknown supplier {supplier_id!r}")
        self._seq += 1
        order = PurchaseOrder(
            order_id    = f"PO-{self._seq:05d}",
            supplier_id = supplier_id,
            order_date  = order_date,
            items       = list(items),
        )
        self.open_orders.append(order)
        return order

    # ---- daily tick -----------------------------------------------------

    def tick(self, today: _dt.date, *, cash_balance_cents: int) -> dict[str, Any]:
        """Run cutoff + payment events for `today` and return a summary."""
        cut: list[PendingInvoice] = []
        for supplier_id, contract in self.contracts.items():
            if is_cutoff_day(today, contract):
                inv = self._cut_invoices_for(supplier_id, today)
                if inv is not None:
                    cut.append(inv)

        paid: list[PendingInvoice] = []
        cash_after = cash_balance_cents
        for inv in list(self.invoices):
            if not inv.paid and inv.due_date <= today:
                if cash_after < inv.total_cents:
                    raise InsufficientFundsError(inv.invoice_id, inv.total_cents - cash_after)
                cash_after -= inv.total_cents
                inv.paid = True
                inv.paid_date = today
                self.invoices.remove(inv)
                self.history.append(inv)
                paid.append(inv)

        return {"cut": cut, "paid": paid, "cash_after_cents": cash_after}

    def _cut_invoices_for(self, supplier_id: str, today: _dt.date) -> PendingInvoice | None:
        contract = self.contracts[supplier_id]
        capturable = [o for o in self.open_orders
                      if o.supplier_id == supplier_id and not o.invoiced and o.order_date <= today]
        if not capturable:
            return None

        # Aggregate identical SKUs.
        line_totals: dict[str, tuple[int, int]] = {}   # sku -> (qty, unit_cost_cents)
        for order in capturable:
            for sku, qty in order.items:
                unit = contract.sku_unit_costs_cents.get(sku, 0)
                if sku in line_totals:
                    prev_qty, prev_unit = line_totals[sku]
                    line_totals[sku] = (prev_qty + qty, prev_unit or unit)
                else:
                    line_totals[sku] = (qty, unit)
            order.invoiced = True

        lines = [InvoiceLine(sku=s, quantity=q, unit_cost_cents=u) for s, (q, u) in line_totals.items()]
        subtotal = sum(l.line_total_cents for l in lines)
        tax = round(subtotal * contract.consumption_tax_rate)
        fee = contract.transfer_fee_cents_buyer
        total = subtotal + tax + fee

        self._seq += 1
        invoice = PendingInvoice(
            invoice_id          = f"INV-{self._seq:05d}",
            supplier_id         = supplier_id,
            cutoff_date         = today,
            due_date            = payment_date_for_cutoff(today, contract),
            lines               = lines,
            subtotal_cents      = subtotal,
            tax_cents           = tax,
            transfer_fee_cents  = fee,
            total_cents         = total,
        )
        self.invoices.append(invoice)
        return invoice

    # ---- summaries ------------------------------------------------------

    def payable_total_cents(self) -> int:
        return sum(inv.total_cents for inv in self.invoices if not inv.paid)

    def next_due(self) -> PendingInvoice | None:
        unpaid = [inv for inv in self.invoices if not inv.paid]
        return min(unpaid, key=lambda i: i.due_date) if unpaid else None

    def summary_lines(self) -> list[str]:
        if not self.contracts:
            return ["- 供货合同：无（即金交易）。"]
        lines = [f"- 供货合同：{len(self.contracts)} 件"]
        for c in self.contracts.values():
            lines.append(
                f"  · {c.label}（{c.supplier_id}）: "
                f"{c.cutoff_day if c.cutoff_day != 31 else '末'}締め"
                f"翌{'当' if c.payment_offset_months == 0 else '翌' if c.payment_offset_months == 2 else ''}月"
                f"{c.payment_day if c.payment_day != 31 else '末'}払い，"
                f"消費税 {c.consumption_tax_rate * 100:.0f}%"
            )
        ap = self.payable_total_cents()
        lines.append(f"- 買掛金合計：¥{ap / 100:.0f}")
        nxt = self.next_due()
        if nxt is not None:
            lines.append(f"- 次回支払：{nxt.due_date} ¥{nxt.total_cents / 100:.0f}（{nxt.supplier_id}）")
        return lines

    # ---- persistence ----------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "_seq": self._seq,
            "contracts": {k: asdict(v) for k, v in self.contracts.items()},
            "open_orders": [asdict(o) for o in self.open_orders],
            "invoices":    [_invoice_to_dict(i) for i in self.invoices],
            "history":     [_invoice_to_dict(i) for i in self.history],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SupplierLedger":
        ledger = cls()
        ledger._seq = int(data.get("_seq", 0))
        for k, v in data.get("contracts", {}).items():
            ledger.contracts[k] = SupplierContract(**v)
        for o in data.get("open_orders", []):
            o = dict(o)
            o["order_date"] = _dt.date.fromisoformat(o["order_date"])
            o["items"] = [tuple(x) for x in o["items"]]
            ledger.open_orders.append(PurchaseOrder(**o))
        for i in data.get("invoices", []):
            ledger.invoices.append(_invoice_from_dict(i))
        for i in data.get("history", []):
            ledger.history.append(_invoice_from_dict(i))
        return ledger

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), default=str, sort_keys=True, indent=2))

    @classmethod
    def load(cls, path: Path) -> "SupplierLedger":
        if not path.exists():
            return cls()
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))


def _invoice_to_dict(inv: PendingInvoice) -> dict[str, Any]:
    d = asdict(inv)
    d["lines"] = [asdict(l) for l in inv.lines]
    return d


def _invoice_from_dict(data: dict[str, Any]) -> PendingInvoice:
    data = dict(data)
    data["cutoff_date"] = _dt.date.fromisoformat(str(data["cutoff_date"]))
    data["due_date"]    = _dt.date.fromisoformat(str(data["due_date"]))
    if data.get("paid_date"):
        data["paid_date"] = _dt.date.fromisoformat(str(data["paid_date"]))
    data["lines"] = [InvoiceLine(**l) for l in data["lines"]]
    return PendingInvoice(**data)
