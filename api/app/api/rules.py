from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from fastapi import Query
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Rule
from ..schemas import RuleApplicationOut, RuleApplyRequest, RuleCreate, RuleOut
from ..services.rules_engine import apply_rules_in_session, preview_rule_application_in_session

router = APIRouter(prefix="/rules", tags=["rules"])


@router.get("/", response_model=list[RuleOut])
def list_rules(db: Session = Depends(get_db)):
    return db.query(Rule).order_by(Rule.priority.asc(), Rule.created_at.asc(), Rule.id.asc()).all()


@router.post("/", response_model=RuleOut)
def create_rule(payload: RuleCreate, db: Session = Depends(get_db)):
    item = Rule(**payload.model_dump())
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.get("/preview", response_model=RuleApplicationOut)
def preview_rules_application(
    q: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    direction: str | None = None,
    meaning: str | None = None,
    category: str | None = None,
    include_transfers: bool = False,
    limit: int = Query(5000, ge=1, le=50000),
    offset: int = Query(0, ge=0),
    sample_limit: int = Query(20, ge=0, le=200),
    db: Session = Depends(get_db),
):
    result = preview_rule_application_in_session(
        db,
        q=q,
        start=start,
        end=end,
        direction=direction,
        meaning=meaning,
        category=category,
        include_transfers=include_transfers,
        limit=limit,
        offset=offset,
        sample_limit=sample_limit,
    )
    return RuleApplicationOut(**{**result.__dict__, "sample": [s.__dict__ for s in result.sample]})


@router.post("/apply", response_model=RuleApplicationOut)
def apply_rules(payload: RuleApplyRequest, db: Session = Depends(get_db)):
    result = apply_rules_in_session(db, **payload.model_dump())
    if not payload.dry_run:
        db.commit()
    return RuleApplicationOut(**{**result.__dict__, "sample": [s.__dict__ for s in result.sample]})
