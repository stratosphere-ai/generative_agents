"""Capital-pool status endpoint."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_session
from ..deps import pool_snapshot
from ..schemas import PoolOut

router = APIRouter(prefix="/api", tags=["pool"])


@router.get("/pool", response_model=PoolOut)
def get_pool(session: Session = Depends(get_session)):
    snap = pool_snapshot(session)
    session.commit()
    return snap
