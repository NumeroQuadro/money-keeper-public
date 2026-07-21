from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Account
from ..schemas import AccountCreate, AccountOut

router = APIRouter(prefix="/accounts", tags=["accounts"])


@router.get("/", response_model=list[AccountOut])
def list_accounts(db: Session = Depends(get_db)):
    return db.query(Account).order_by(Account.created_at.desc()).all()


@router.post("/", response_model=AccountOut)
def create_account(payload: AccountCreate, db: Session = Depends(get_db)):
    item = Account(**payload.model_dump())
    db.add(item)
    db.commit()
    db.refresh(item)
    return item
