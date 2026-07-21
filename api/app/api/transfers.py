from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, func, or_
from sqlalchemy.orm import Session, aliased

from ..db import get_db
from ..models import TransferLink, Transaction
from ..schemas import TransferDetectOut, TransferLinkOut
from ..services.transfers import (
    confirm_transfer_link,
    detect_transfer_links_in_session,
    rebuild_all_transfer_links_in_session,
    reject_transfer_link,
)

router = APIRouter(prefix="/transfers", tags=["transfers"])


@router.get("/links", response_model=list[TransferLinkOut])
def list_transfer_links(
    status: Annotated[
        str | None,
        Query(description="Filter by status (auto/suggested/confirmed/rejected)"),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
    db: Session = Depends(get_db),
):
    tx_out = aliased(Transaction)
    tx_in = aliased(Transaction)

    out_event_at = func.coalesce(
        tx_out.operation_datetime,
        tx_out.posting_datetime,
        tx_out.created_at,
    )
    in_event_at = func.coalesce(
        tx_in.operation_datetime,
        tx_in.posting_datetime,
        tx_in.created_at,
    )
    link_event_at = case(
        (out_event_at.is_(None), in_event_at),
        (in_event_at.is_(None), out_event_at),
        (out_event_at >= in_event_at, out_event_at),
        else_=in_event_at,
    )
    sort_at = func.coalesce(link_event_at, TransferLink.created_at)

    out_source_statement = func.coalesce(tx_out.source_statement_id, "")
    in_source_statement = func.coalesce(tx_in.source_statement_id, "")
    source_statement_sort = case(
        (out_source_statement >= in_source_statement, out_source_statement),
        else_=in_source_statement,
    )
    out_source_page = func.coalesce(tx_out.source_page_number, -1)
    in_source_page = func.coalesce(tx_in.source_page_number, -1)
    source_page_sort = case(
        (out_source_page >= in_source_page, out_source_page),
        else_=in_source_page,
    )
    out_source_row = func.coalesce(tx_out.source_row_index, -1)
    in_source_row = func.coalesce(tx_in.source_row_index, -1)
    source_row_sort = case(
        (out_source_row >= in_source_row, out_source_row),
        else_=in_source_row,
    )

    out_is_date_only = func.coalesce(tx_out.timestamp_precision, "unknown").in_(
        ["date_only", "unknown"]
    )
    in_is_date_only = func.coalesce(tx_in.timestamp_precision, "unknown").in_(
        ["date_only", "unknown"]
    )
    has_date_only = or_(out_is_date_only, in_is_date_only)
    date_only_tiebreak_statement = case((has_date_only, source_statement_sort), else_="")
    date_only_tiebreak_page = case((has_date_only, source_page_sort), else_=-1)
    date_only_tiebreak_row = case((has_date_only, source_row_sort), else_=-1)

    query = (
        db.query(TransferLink)
        .outerjoin(tx_out, tx_out.id == TransferLink.transaction_out_id)
        .outerjoin(tx_in, tx_in.id == TransferLink.transaction_in_id)
    )
    if status:
        query = query.filter(TransferLink.status == status)
    return (
        query.order_by(
            sort_at.desc(),
            date_only_tiebreak_statement.desc(),
            date_only_tiebreak_page.desc(),
            date_only_tiebreak_row.desc(),
            TransferLink.created_at.desc(),
            TransferLink.id.desc(),
        )
        .offset(offset)
        .limit(limit)
        .all()
    )


@router.post("/rebuild", response_model=TransferDetectOut)
def rebuild_transfer_links(db: Session = Depends(get_db)):
    result = detect_transfer_links_in_session(db)
    db.commit()
    return TransferDetectOut(**result.__dict__)


@router.post("/rebuild-all", response_model=TransferDetectOut)
def rebuild_all_transfer_links(db: Session = Depends(get_db)):
    result = rebuild_all_transfer_links_in_session(db)
    db.commit()
    return TransferDetectOut(**result.__dict__)


@router.post("/links/{link_id}/confirm", response_model=TransferLinkOut)
def confirm_link(link_id: str, db: Session = Depends(get_db)):
    link = db.query(TransferLink).filter(TransferLink.id == link_id).first()
    if not link:
        raise HTTPException(status_code=404, detail="Transfer link not found")
    confirm_transfer_link(db, link=link)
    db.commit()
    db.refresh(link)
    return link


@router.post("/links/{link_id}/reject", response_model=TransferLinkOut)
def reject_link(link_id: str, db: Session = Depends(get_db)):
    link = db.query(TransferLink).filter(TransferLink.id == link_id).first()
    if not link:
        raise HTTPException(status_code=404, detail="Transfer link not found")
    reject_transfer_link(db, link=link)
    db.commit()
    db.refresh(link)
    return link
