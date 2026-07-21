from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..schemas import NetWorthCurrentOut, NetWorthRebuildOut, NetWorthTimelineOut
from ..services.net_worth import (
    compute_net_worth_current,
    compute_net_worth_timeline,
    rebuild_net_worth_artifacts_in_session,
)

router = APIRouter(prefix="/networth", tags=["networth"])


@router.get("/current", response_model=NetWorthCurrentOut)
def get_net_worth_current(currency: Optional[str] = None, db: Session = Depends(get_db)):
    return compute_net_worth_current(db, currency=currency)


@router.get("/timeline", response_model=NetWorthTimelineOut)
def get_net_worth_timeline(
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    currency: Optional[str] = None,
    granularity: Optional[str] = "raw",
    db: Session = Depends(get_db),
):
    return compute_net_worth_timeline(
        db, start=start, end=end, currency=currency, granularity=granularity or "raw"
    )


@router.post("/rebuild", response_model=NetWorthRebuildOut)
def rebuild_net_worth(db: Session = Depends(get_db)):
    result = rebuild_net_worth_artifacts_in_session(db)
    db.commit()
    return result.__dict__
