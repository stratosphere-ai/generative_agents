"""Admin: view exposure and resolve markets (triggers claim settlement)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import models, settlement
from ..db import get_session
from ..deps import pool_snapshot
from ..models import HedgeLeg, Market, Policy
from ..schemas import AdminMarketOut, ResolveRequest, ResolveResponse

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/markets", response_model=list[AdminMarketOut])
def admin_markets(session: Session = Depends(get_session)):
    markets = session.scalars(select(Market).order_by(Market.id)).all()
    out = []
    for m in markets:
        pol_count = session.scalar(
            select(func.count(Policy.id)).where(
                Policy.market_id == m.id, Policy.status == models.POLICY_ACTIVE
            )
        ) or 0
        pol_exposure = session.scalar(
            select(func.coalesce(func.sum(Policy.coverage), 0.0)).where(
                Policy.market_id == m.id, Policy.status == models.POLICY_ACTIVE
            )
        ) or 0.0
        leg_count = session.scalar(
            select(func.count(HedgeLeg.id)).where(
                HedgeLeg.market_id == m.id, HedgeLeg.status == models.LEG_ACTIVE
            )
        ) or 0
        leg_exposure = session.scalar(
            select(func.coalesce(func.sum(HedgeLeg.covered_loss), 0.0)).where(
                HedgeLeg.market_id == m.id, HedgeLeg.status == models.LEG_ACTIVE
            )
        ) or 0.0
        count = int(pol_count) + int(leg_count)
        exposure = float(pol_exposure) + float(leg_exposure)
        item = AdminMarketOut(
            id=m.id,
            provider=m.provider,
            external_id=m.external_id,
            question=m.question,
            description=m.description,
            probability=m.probability,
            end_date=m.end_date,
            status=m.status,
            active_policies=int(count),
            total_exposure=round(float(exposure), 2),
            resolvable=m.status == models.MARKET_OPEN,
        )
        out.append(item)
    return out


@router.post("/resolve", response_model=ResolveResponse)
def resolve(req: ResolveRequest, session: Session = Depends(get_session)):
    market = session.get(Market, req.market_id)
    if market is None:
        raise HTTPException(status_code=404, detail="market not found")
    try:
        result = settlement.resolve_market(session, market, req.outcome)
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc))

    pool = pool_snapshot(session)
    session.commit()
    return ResolveResponse(
        market_id=result.market_id,
        outcome=result.outcome,
        policies_paid=result.policies_paid,
        policies_expired=result.policies_expired,
        legs_paid=result.legs_paid,
        legs_expired=result.legs_expired,
        total_payout=round(result.total_payout, 2),
        pool_after=pool,
    )
