"""SQLAlchemy ORM models.

The append-only ``LedgerEntry`` journal is the source of truth for pool balances;
``CapitalPool`` is a materialized cache recomputed from the ledger on every mutation.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


# --- Market statuses ---
MARKET_OPEN = "open"
MARKET_RESOLVED_YES = "resolved_yes"
MARKET_RESOLVED_NO = "resolved_no"
MARKET_CLOSED = "closed"

# --- Policy statuses ---
POLICY_ACTIVE = "active"
POLICY_PAID_OUT = "paid_out"
POLICY_EXPIRED_WORTHLESS = "expired_worthless"

# --- Basket (cargo) policy statuses ---
BASKET_ACTIVE = "active"
BASKET_PARTIALLY_SETTLED = "partially_settled"
BASKET_SETTLED = "settled"

# --- Hedge leg statuses (mirror single-event policy statuses) ---
LEG_ACTIVE = "active"
LEG_PAID_OUT = "paid_out"
LEG_EXPIRED_WORTHLESS = "expired_worthless"

# --- Risk-factor categories ---
CAT_ROUTE = "route"
CAT_GEOPOLITICAL = "geopolitical"
CAT_MACRO = "macro"
CAT_WEATHER = "weather"

# --- Ledger entry types (see ledger.py for sign convention) ---
ENTRY_SEED = "seed_capital"
ENTRY_PREMIUM_IN = "premium_in"
ENTRY_HEDGE_BUY = "hedge_buy"
ENTRY_RESERVE_ADD = "reserve_add"
ENTRY_RESERVE_RELEASE = "reserve_release"
ENTRY_PAYOUT = "payout"
ENTRY_HEDGE_SETTLE = "hedge_settle"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, default="demo")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    policies: Mapped[list["Policy"]] = relationship(back_populates="user")


class Market(Base):
    """Cached snapshot of an insurable prediction-market event."""

    __tablename__ = "markets"
    __table_args__ = (UniqueConstraint("provider", "external_id", name="uq_provider_external"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(32))
    external_id: Mapped[str] = mapped_column(String(128))
    question: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    probability: Mapped[float] = mapped_column(Float)  # YES-implied probability p
    end_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(24), default=MARKET_OPEN)
    synced_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    policies: Mapped[list["Policy"]] = relationship(back_populates="market")


class Policy(Base):
    __tablename__ = "policies"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id"))

    coverage: Mapped[float] = mapped_column(Float)  # C, payout if event resolves YES
    probability_at_purchase: Mapped[float] = mapped_column(Float)  # p used for pricing
    loading_factor: Mapped[float] = mapped_column(Float)
    premium: Mapped[float] = mapped_column(Float)  # charged to policyholder
    reserve: Mapped[float] = mapped_column(Float)  # liability held (= coverage)
    hedge_shares: Mapped[float] = mapped_column(Float)  # YES shares "bought" (= coverage)
    hedge_price: Mapped[float] = mapped_column(Float)  # price paid per share (= p)

    status: Mapped[str] = mapped_column(String(24), default=POLICY_ACTIVE)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped["User"] = relationship(back_populates="policies")
    market: Mapped["Market"] = relationship(back_populates="policies")


class Shipment(Base):
    """The real-world insured object: a cargo that must arrive on time."""

    __tablename__ = "shipments"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    cargo_value: Mapped[float] = mapped_column(Float)  # insured value, e.g. 400_000
    origin: Mapped[str] = mapped_column(String(128))
    destination: Mapped[str] = mapped_column(String(128))
    deadline: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cargo_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    route: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    policy: Mapped["BasketPolicy"] = relationship(back_populates="shipment", uselist=False)


class BasketPolicy(Base):
    """One policy covering a shipment against many correlated risk factors.

    From the buyer's view it is a single premium; internally it fans out into a
    portfolio of HedgeLegs, one per prediction-market book we bet on.
    """

    __tablename__ = "basket_policies"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    shipment_id: Mapped[int] = mapped_column(ForeignKey("shipments.id"))
    premium: Mapped[float] = mapped_column(Float)  # total charged
    total_covered: Mapped[float] = mapped_column(Float)  # sum of leg covered losses
    total_reserve: Mapped[float] = mapped_column(Float)  # liability held
    loading_factor: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(24), default=BASKET_ACTIVE)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    shipment: Mapped["Shipment"] = relationship(back_populates="policy")
    legs: Mapped[list["HedgeLeg"]] = relationship(
        back_populates="policy", cascade="all, delete-orphan"
    )


class HedgeLeg(Base):
    """One risk factor within a basket policy, hedged via one market book."""

    __tablename__ = "hedge_legs"

    id: Mapped[int] = mapped_column(primary_key=True)
    basket_policy_id: Mapped[int] = mapped_column(ForeignKey("basket_policies.id"))
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id"))

    factor_key: Mapped[str] = mapped_column(String(64))
    factor_label: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(24))
    impact: Mapped[float] = mapped_column(Float)  # loss fraction of cargo value
    covered_loss: Mapped[float] = mapped_column(Float)  # impact * cargo_value
    probability_at_purchase: Mapped[float] = mapped_column(Float)
    premium: Mapped[float] = mapped_column(Float)  # leg's share of the premium
    hedge_shares: Mapped[float] = mapped_column(Float)  # = covered_loss
    hedge_price: Mapped[float] = mapped_column(Float)  # = probability
    status: Mapped[str] = mapped_column(String(24), default=LEG_ACTIVE)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    policy: Mapped["BasketPolicy"] = relationship(back_populates="legs")
    market: Mapped["Market"] = relationship()


class CapitalPool(Base):
    """Singleton (id=1) cache of pool balances, derived from the ledger."""

    __tablename__ = "capital_pool"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    cash_balance: Mapped[float] = mapped_column(Float, default=0.0)
    reserved_liabilities: Mapped[float] = mapped_column(Float, default=0.0)
    hedge_asset_value: Mapped[float] = mapped_column(Float, default=0.0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class LedgerEntry(Base):
    """Append-only journal. Never updated or deleted."""

    __tablename__ = "ledger_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    policy_id: Mapped[int | None] = mapped_column(ForeignKey("policies.id"), nullable=True)
    basket_policy_id: Mapped[int | None] = mapped_column(
        ForeignKey("basket_policies.id"), nullable=True
    )
    entry_type: Mapped[str] = mapped_column(String(32))
    amount: Mapped[float] = mapped_column(Float)  # signed: inflow +, outflow -
    memo: Mapped[str] = mapped_column(String(255), default="")
