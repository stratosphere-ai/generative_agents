"""Shared test fixtures: an in-memory DB seeded with a pool and a market."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app import ledger, models
from app.models import Base, Market, User


@pytest.fixture
def session() -> Session:
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, future=True
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    s = SessionLocal()
    ledger.seed_pool(s, 10_000.0)
    s.commit()
    yield s
    s.close()


@pytest.fixture
def user(session) -> User:
    u = User(username="demo")
    session.add(u)
    session.flush()
    return u


@pytest.fixture
def market(session) -> Market:
    m = Market(
        provider="mock",
        external_id="t1",
        question="Will it rain?",
        probability=0.40,
        status=models.MARKET_OPEN,
    )
    session.add(m)
    session.flush()
    return m
