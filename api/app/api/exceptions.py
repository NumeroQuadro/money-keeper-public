from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import ExceptionItem, ReviewStatus, Transaction
from ..schemas import ExceptionOut

router = APIRouter(prefix="/exceptions", tags=["exceptions"])


def _set_resolution(item: ExceptionItem, *, resolution: str) -> None:
    payload = dict(item.payload) if isinstance(item.payload, dict) else {}
    payload["resolution"] = resolution
    item.payload = payload
    item.status = "resolved"
    item.resolved_at = datetime.now(timezone.utc)


def _suggested_category(*, item: ExceptionItem, tx: Transaction) -> str:
    payload = item.payload if isinstance(item.payload, dict) else {}
    for key in ("suggested_category", "category", "after_category"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    suggested = payload.get("suggested")
    if isinstance(suggested, dict):
        value = suggested.get("category")
        if isinstance(value, str) and value.strip():
            return value.strip()

    bank_category = (tx.bank_category or "").strip()
    if bank_category:
        return bank_category[:1].upper() + bank_category[1:]
    return "Uncategorized"


def _normalize_tags(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        if item is None:
            continue
        tag = str(item).strip()
        if not tag:
            continue
        if tag in result:
            continue
        result.append(tag)
    return result


@router.get("/", response_model=list[ExceptionOut])
def list_exceptions(
    status: Literal["all", "open", "resolved"] = Query(default="all"),
    db: Session = Depends(get_db),
):
    query = db.query(ExceptionItem)
    if status != "all":
        query = query.filter(ExceptionItem.status == status)
    return query.order_by(ExceptionItem.created_at.desc()).all()


@router.post("/{exception_id}/resolve", response_model=ExceptionOut)
def resolve_exception(exception_id: str, db: Session = Depends(get_db)):
    item = db.query(ExceptionItem).filter(ExceptionItem.id == exception_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Exception not found")
    _set_resolution(item, resolution="resolved")
    db.commit()
    db.refresh(item)
    return item


@router.post("/{exception_id}/ignore", response_model=ExceptionOut)
def ignore_exception(exception_id: str, db: Session = Depends(get_db)):
    item = db.query(ExceptionItem).filter(ExceptionItem.id == exception_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Exception not found")
    _set_resolution(item, resolution="ignored")
    db.commit()
    db.refresh(item)
    return item


@router.post("/{exception_id}/approve-category", response_model=ExceptionOut)
def approve_exception_category(exception_id: str, db: Session = Depends(get_db)):
    item = db.query(ExceptionItem).filter(ExceptionItem.id == exception_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Exception not found")
    if item.entity_type != "transaction":
        raise HTTPException(
            status_code=400, detail="Approve category is only supported for transaction exceptions"
        )

    tx = db.query(Transaction).filter(Transaction.id == item.entity_id).first()
    if not tx:
        raise HTTPException(status_code=404, detail="Referenced transaction not found")

    tx.category = _suggested_category(item=item, tx=tx)
    tx.review_status = ReviewStatus.reviewed.value
    _set_resolution(item, resolution="category_approved")
    db.commit()
    db.refresh(item)
    return item


@router.post("/{exception_id}/mark-duplicate", response_model=ExceptionOut)
def mark_exception_duplicate(exception_id: str, db: Session = Depends(get_db)):
    item = db.query(ExceptionItem).filter(ExceptionItem.id == exception_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Exception not found")

    if item.entity_type == "transaction" and item.entity_id:
        tx = db.query(Transaction).filter(Transaction.id == item.entity_id).first()
        if tx:
            tags = _normalize_tags(tx.tags)
            if "duplicate" not in tags:
                tags.append("duplicate")
            tx.tags = tags
            tx.review_status = ReviewStatus.reviewed.value
            if not (tx.category or "").strip():
                tx.category = "Duplicate"

    _set_resolution(item, resolution="marked_duplicate")
    db.commit()
    db.refresh(item)
    return item
