"""Market browsing endpoints. Reads from the provider and caches to the DB."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_session
from ..deps import provider, upsert_market
from ..schemas import MarketOut

router = APIRouter(prefix="/api/markets", tags=["markets"])


@router.get("", response_model=list[MarketOut])
def list_markets(limit: int = 50, session: Session = Depends(get_session)):
    prov = provider()
    markets = []
    for md in prov.list_markets(limit=limit):
        markets.append(upsert_market(session, md, prov.name.split("->")[0]))
    session.commit()
    return markets


@router.get("/{market_id}", response_model=MarketOut)
def get_market(market_id: int, session: Session = Depends(get_session)):
    from ..models import Market

    market = session.get(Market, market_id)
    if market is None:
        raise HTTPException(status_code=404, detail="market not found")
    return market
