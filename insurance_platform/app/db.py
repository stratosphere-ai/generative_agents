"""Database engine / session wiring (SQLAlchemy 2.x + SQLite)."""
from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from .config import DATA_DIR, settings

# check_same_thread=False lets the file DB be used across FastAPI's thread pool.
_connect_args = (
    {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
)
engine = create_engine(settings.database_url, connect_args=_connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db() -> None:
    """Create tables and seed the singleton capital pool if needed."""
    from . import models  # noqa: F401  (register mappers)
    from .ledger import seed_pool

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    models.Base.metadata.create_all(engine)
    with SessionLocal() as session:
        seed_pool(session, settings.seed_capital)
        session.commit()


def get_session() -> Iterator[Session]:
    """FastAPI dependency yielding a request-scoped session."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
