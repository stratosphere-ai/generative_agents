"""Quote, one-click buy (一键投保), and policy listing endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import pricing, settlement
from ..config import settings
from ..db import get_session
from ..deps import get_demo_user, pool_snapshot
from ..models import Market, Policy
from ..schemas import BuyResponse, PolicyOut, QuoteRequest, QuoteResponse

router = APIRouter(prefix="/api", tags=["policies"])


def _load_market(session: Session, market_id: int) -> Market:
    market = session.get(Market, market_id)
    if market is None:
        raise HTTPException(status_code=404, detail="market not found")
    return market


@router.post("/quote", response_model=QuoteResponse)
def quote(req: QuoteRequest, session: Session = Depends(get_session)):
    market = _load_market(session, req.market_id)
    p = pricing.clamp_probability(market.probability)
    return QuoteResponse(
        market_id=market.id,
        question=market.question,
        coverage=req.coverage,
        probability=p,
        loading_factor=settings.loading_factor,
        premium=round(pricing.calc_premium(req.coverage, p, settings.loading_factor), 2),
        reserve=round(pricing.calc_reserve(req.coverage), 2),
    )


@router.post("/policies", response_model=BuyResponse)
def buy(req: QuoteRequest, session: Session = Depends(get_session)):
    """One-click投保: price, solvency-check, issue, and settle the ledger."""
    market = _load_market(session, req.market_id)
    user = get_demo_user(session)
    try:
        policy = settlement.underwrite_policy(
            session,
            user,
            market,
            req.coverage,
            settings.loading_factor,
            settings.min_solvency,
        )
    except settlement.InsolvencyError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc))

    pool = pool_snapshot(session)
    session.commit()
    return BuyResponse(
        policy_id=policy.id,
        premium=round(policy.premium, 2),
        coverage=policy.coverage,
        status=policy.status,
        pool_after=pool,
    )


@router.get("/policies", response_model=list[PolicyOut])
def my_policies(session: Session = Depends(get_session)):
    user = get_demo_user(session)
    rows = session.scalars(
        select(Policy).where(Policy.user_id == user.id).order_by(Policy.id.desc())
    ).all()
    out = []
    for p in rows:
        item = PolicyOut.model_validate(p)
        item.question = p.market.question if p.market else None
        out.append(item)
    session.commit()
    return out
