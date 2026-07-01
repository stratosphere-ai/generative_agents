"""Cargo insurance: analyze a shipment, one-click buy a basket policy, list them.

The buyer's experience is unchanged from the single-event product — submit the
shipment, see one premium, click once. The engine (discovery -> real-book
matching -> pricing) fans the shipment out into a hedged portfolio behind the
scenes; the responses expose which factors came from the LLM vs the rule engine
and which were mapped to a real order book.
"""
from __future__ import annotations

from types import SimpleNamespace

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import engine, settlement
from ..config import settings
from ..db import get_session
from ..deps import get_demo_user, pool_snapshot
from ..models import BasketPolicy, Shipment
from ..schemas import (
    BasketBuyResponse,
    BasketPolicyOut,
    FactorOut,
    HedgeLegOut,
    ShipmentQuoteRequest,
    ShipmentQuoteResponse,
)

router = APIRouter(prefix="/api/shipments", tags=["shipments"])


def _assess(req: ShipmentQuoteRequest):
    shipment_like = SimpleNamespace(
        origin=req.origin,
        destination=req.destination,
        route=req.route,
        cargo_type=req.cargo_type,
        deadline=req.deadline,
    )
    return engine.assess(shipment_like, req.cargo_value, settings.loading_factor)


@router.post("/quote", response_model=ShipmentQuoteResponse)
def quote(req: ShipmentQuoteRequest):
    """Analyze the shipment and return the single premium + factor breakdown."""
    assessments = _assess(req)
    if not assessments:
        raise HTTPException(status_code=400, detail="no insurable factors for this route")
    factors = [
        FactorOut(
            key=a.key, label=a.label, category=a.category,
            probability=round(a.probability, 4), impact=a.impact,
            covered_loss=round(a.covered_loss, 2), expected_loss=round(a.expected_loss, 2),
            hedge_cost=round(a.hedge_cost, 2), premium=round(a.premium, 2),
            source=a.source, market_provider=a.market_provider, matched=a.market_matched,
            market_liquidity=round(a.market_liquidity, 2),
            hedge_shares=round(a.hedge_shares, 2),
        )
        for a in assessments
    ]
    return ShipmentQuoteResponse(
        cargo_value=req.cargo_value,
        origin=req.origin,
        destination=req.destination,
        loading_factor=settings.loading_factor,
        premium=round(sum(a.premium for a in assessments), 2),
        total_covered=round(sum(a.covered_loss for a in assessments), 2),
        expected_loss_total=round(sum(a.expected_loss for a in assessments), 2),
        factor_count=len(assessments),
        discovery_mode=assessments[0].source,
        matched_count=sum(1 for a in assessments if a.market_matched),
        factors=factors,
    )


@router.post("/policies", response_model=BasketBuyResponse)
def buy(req: ShipmentQuoteRequest, session: Session = Depends(get_session)):
    """One-click投保: create the shipment, hedge every factor, issue one policy."""
    assessments = _assess(req)
    if not assessments:
        raise HTTPException(status_code=400, detail="no insurable factors for this route")

    user = get_demo_user(session)
    deadline = req.deadline.replace(tzinfo=None) if req.deadline else None
    shipment = Shipment(
        user_id=user.id,
        cargo_value=req.cargo_value,
        origin=req.origin,
        destination=req.destination,
        deadline=deadline,
        cargo_type=req.cargo_type,
        route=req.route,
    )
    session.add(shipment)
    session.flush()

    try:
        policy = settlement.underwrite_basket(
            session, user, shipment, assessments,
            settings.loading_factor, settings.min_solvency,
        )
    except settlement.InsolvencyError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc))

    pool = pool_snapshot(session)
    session.commit()
    return BasketBuyResponse(
        policy_id=policy.id,
        premium=round(policy.premium, 2),
        total_covered=round(policy.total_covered, 2),
        factor_count=len(policy.legs),
        status=policy.status,
        pool_after=pool,
    )


@router.get("/policies", response_model=list[BasketPolicyOut])
def my_basket_policies(session: Session = Depends(get_session)):
    user = get_demo_user(session)
    rows = session.scalars(
        select(BasketPolicy)
        .where(BasketPolicy.user_id == user.id)
        .order_by(BasketPolicy.id.desc())
    ).all()
    out = []
    for p in rows:
        sh = p.shipment
        legs = []
        for l in p.legs:
            item = HedgeLegOut.model_validate(l)
            item.market_provider = l.market.provider if l.market else "engine"
            legs.append(item)
        out.append(
            BasketPolicyOut(
                id=p.id,
                premium=round(p.premium, 2),
                total_covered=round(p.total_covered, 2),
                total_reserve=round(p.total_reserve, 2),
                status=p.status,
                created_at=p.created_at,
                cargo_value=sh.cargo_value,
                origin=sh.origin,
                destination=sh.destination,
                deadline=sh.deadline,
                cargo_type=sh.cargo_type,
                legs=legs,
            )
        )
    session.commit()
    return out
