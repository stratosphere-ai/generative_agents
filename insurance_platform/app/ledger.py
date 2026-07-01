"""Append-only ledger and pool recomputation.

Sign convention (all amounts stored signed on LedgerEntry.amount):
    inflows  are POSITIVE  (seed_capital, premium_in, hedge_settle)
    outflows are NEGATIVE  (hedge_buy, payout)
    reserve_add / reserve_release carry the (positive/negative) change to the
        reserved-liabilities figure and do NOT affect cash.

    cash_balance         = sum(amount) over cash entry types
    reserved_liabilities = sum(amount) over reserve_add + reserve_release entries
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models
from .models import CapitalPool, LedgerEntry

_CASH_TYPES = {
    models.ENTRY_SEED,
    models.ENTRY_PREMIUM_IN,
    models.ENTRY_HEDGE_BUY,
    models.ENTRY_PAYOUT,
    models.ENTRY_HEDGE_SETTLE,
}
_RESERVE_TYPES = {models.ENTRY_RESERVE_ADD, models.ENTRY_RESERVE_RELEASE}


def post(
    session: Session,
    entry_type: str,
    amount: float,
    policy_id: int | None = None,
    memo: str = "",
    basket_policy_id: int | None = None,
) -> LedgerEntry:
    """Append a journal entry. Does not commit — caller controls the transaction."""
    entry = LedgerEntry(
        entry_type=entry_type,
        amount=amount,
        policy_id=policy_id,
        basket_policy_id=basket_policy_id,
        memo=memo,
    )
    session.add(entry)
    session.flush()
    return entry


def _get_pool(session: Session) -> CapitalPool:
    pool = session.get(CapitalPool, 1)
    if pool is None:
        pool = CapitalPool(id=1)
        session.add(pool)
        session.flush()
    return pool


def recompute_pool(session: Session) -> CapitalPool:
    """Fold the entire ledger into the singleton pool cache."""
    entries = session.scalars(select(LedgerEntry)).all()
    cash = sum(e.amount for e in entries if e.entry_type in _CASH_TYPES)
    reserved = sum(e.amount for e in entries if e.entry_type in _RESERVE_TYPES)
    # Mark-to-market of held hedge shares: net YES-shares bought are worth their
    # purchase cost until settlement. hedge_buy is negative cash out; its
    # magnitude is the asset value still on the book (released at settle time).
    hedge_out = -sum(
        e.amount for e in entries if e.entry_type == models.ENTRY_HEDGE_BUY
    )
    hedge_settled = sum(
        e.amount for e in entries if e.entry_type == models.ENTRY_HEDGE_SETTLE
    )
    hedge_value = max(0.0, hedge_out - hedge_settled)

    pool = _get_pool(session)
    pool.cash_balance = cash
    pool.reserved_liabilities = reserved
    pool.hedge_asset_value = hedge_value
    pool.updated_at = models.utcnow()
    session.flush()
    return pool


def seed_pool(session: Session, amount: float) -> None:
    """Seed the pool with starting capital on first run (idempotent)."""
    existing = session.scalar(
        select(LedgerEntry).where(LedgerEntry.entry_type == models.ENTRY_SEED)
    )
    if existing is not None:
        return
    post(session, models.ENTRY_SEED, amount, memo="initial capital")
    recompute_pool(session)


def collected_premiums(session: Session) -> float:
    """Total premium ever collected (for reporting)."""
    entries = session.scalars(
        select(LedgerEntry).where(LedgerEntry.entry_type == models.ENTRY_PREMIUM_IN)
    ).all()
    return sum(e.amount for e in entries)
