from __future__ import annotations

from datetime import datetime
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import String, and_, case, cast, func, or_
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from ..db import get_db
from ..domain.cashflow_lens import (
    CASHFLOW_LENS_HIGH_CONFIDENCE_TRANSFER_LIKE,
    CASHFLOW_LENS_STRICT_TRANSFER_LIKE,
    CashflowLens,
    DEFAULT_CASHFLOW_LENS,
    normalize_cashflow_lens,
    transfer_exclusion_params,
)
from ..models import ReviewStatus, Transaction
from ..schemas import TransactionCreate, TransactionList, TransactionOut
from ..review import compute_transaction_review_reasons, transaction_review_expr

router = APIRouter(prefix="/transactions", tags=["transactions"])


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


def _approved_category(tx: Transaction) -> str:
    current_category = (tx.category or "").strip()
    if current_category:
        return current_category

    bank_category = (tx.bank_category or "").strip()
    if bank_category:
        return bank_category[:1].upper() + bank_category[1:]

    return "Uncategorized"


def _serialize_transaction(item: Transaction) -> TransactionOut:
    review_reasons = compute_transaction_review_reasons(item)
    payload = TransactionOut.model_validate(item, from_attributes=True)
    return payload.model_copy(
        update={
            "review_reasons": review_reasons,
            "needs_human_review": bool(review_reasons),
        }
    )


@router.get("/", response_model=TransactionList)
def list_transactions(
    q: Optional[str] = None,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    account_id: Optional[str] = None,
    direction: Optional[str] = None,
    meaning: Optional[str] = None,
    category: Optional[str] = None,
    category_empty: Optional[bool] = None,
    tags: Optional[str] = None,
    review_status: Optional[str] = None,
    needs_human_review: Optional[bool] = None,
    review_reason: Optional[str] = None,
    include_transfers: bool = False,
    cashflow_lens: CashflowLens = DEFAULT_CASHFLOW_LENS,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    db: Session = Depends(get_db),
):
    query = db.query(Transaction)
    normalized_lens = normalize_cashflow_lens(cashflow_lens)

    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(
                Transaction.description_raw.ilike(like),
                Transaction.merchant_normalized.ilike(like),
                Transaction.bank_category.ilike(like),
                Transaction.category.ilike(like),
            )
        )

    if start:
        query = query.filter(Transaction.operation_datetime >= start)
    if end:
        query = query.filter(Transaction.operation_datetime <= end)
    if account_id:
        query = query.filter(Transaction.account_id == account_id)
    if direction:
        query = query.filter(Transaction.direction == direction)
    if meaning:
        query = query.filter(Transaction.meaning == meaning)
    if category:
        query = query.filter(Transaction.category == category)
    if category_empty is not None:
        category_is_empty = func.length(func.trim(func.coalesce(Transaction.category, ""))) == 0
        query = query.filter(category_is_empty if category_empty else ~category_is_empty)
    if tags:
        tag_list = [tag.strip() for tag in tags.split(",") if tag.strip()]
        if tag_list:
            dialect_name = (db.bind.dialect.name if db.bind is not None else "").lower()
            tag_filters = []
            for tag in tag_list:
                if dialect_name == "postgresql":
                    tag_filters.append(cast(Transaction.tags, JSONB).contains([tag]))
                    continue

                escaped_tag = (
                    tag.replace("\\", "\\\\")
                    .replace('"', '\\"')
                    .replace("%", "\\%")
                    .replace("_", "\\_")
                )
                like_pattern = f'%"{escaped_tag}"%'
                tag_filters.append(
                    cast(func.coalesce(Transaction.tags, "[]"), String).like(
                        like_pattern,
                        escape="\\",
                    )
                )

            query = query.filter(or_(*tag_filters))
    if review_status:
        query = query.filter(Transaction.review_status == review_status)
    if needs_human_review is not None or review_reason:
        review_filter = transaction_review_expr(review_reason)
        query = query.filter(review_filter if needs_human_review is not False else ~review_filter)
    if not include_transfers:
        exclusion_params = transfer_exclusion_params()
        exclusion_expr = (
            func.coalesce(Transaction.meaning, "") == exclusion_params["internal_transfer_meaning"]
        )

        if normalized_lens == CASHFLOW_LENS_STRICT_TRANSFER_LIKE:
            description = func.lower(func.coalesce(Transaction.description_raw, ""))
            bank_category = func.lower(func.coalesce(Transaction.bank_category, ""))
            exclusion_expr = or_(
                exclusion_expr,
                func.coalesce(Transaction.meaning, "")
                == exclusion_params["external_transfer_meaning"],
                bank_category == exclusion_params["transfer_bank_category"],
                description.like(exclusion_params["transfer_hint_like_1"]),
                description.like(exclusion_params["transfer_hint_like_2"]),
                description.like(exclusion_params["transfer_hint_like_3"]),
                description.like(exclusion_params["transfer_hint_like_4"]),
                description.like(exclusion_params["transfer_hint_like_5"]),
                description.like(exclusion_params["transfer_hint_like_6"]),
                description.like(exclusion_params["transfer_hint_like_7"]),
                description.like(exclusion_params["transfer_hint_like_8"]),
            )
        elif normalized_lens == CASHFLOW_LENS_HIGH_CONFIDENCE_TRANSFER_LIKE:
            description = func.lower(func.coalesce(Transaction.description_raw, ""))
            bank_category = func.lower(func.coalesce(Transaction.bank_category, ""))
            bank_reference = func.coalesce(Transaction.bank_reference_id, "")
            direction_expr = func.coalesce(Transaction.direction, "")
            exclusion_expr = or_(
                exclusion_expr,
                func.coalesce(Transaction.meaning, "")
                == exclusion_params["external_transfer_meaning"],
                and_(
                    bank_category == exclusion_params["transfer_bank_category"],
                    bank_reference != "",
                    or_(
                        and_(
                            direction_expr == "in",
                            func.coalesce(Transaction.amount, 0)
                            >= exclusion_params["high_conf_transfer_min_in_amount"],
                        ),
                        and_(
                            direction_expr != "in",
                            func.coalesce(Transaction.amount, 0)
                            >= exclusion_params["high_conf_transfer_min_out_amount"],
                        ),
                    ),
                    or_(
                        and_(
                            direction_expr == "in",
                            or_(
                                description.like(exclusion_params["high_conf_transfer_like_in_1"]),
                                description.like(exclusion_params["high_conf_transfer_like_in_2"]),
                                description.like(exclusion_params["high_conf_transfer_like_in_3"]),
                                description.like(exclusion_params["high_conf_transfer_like_in_4"]),
                            ),
                        ),
                        and_(
                            direction_expr != "in",
                            or_(
                                description.like(exclusion_params["high_conf_transfer_like_out_1"]),
                                description.like(exclusion_params["high_conf_transfer_like_out_2"]),
                                description.like(exclusion_params["high_conf_transfer_like_out_3"]),
                            ),
                        ),
                    ),
                ),
            )

        query = query.filter(~exclusion_expr)

    total = query.count()
    event_datetime = func.coalesce(
        Transaction.operation_datetime,
        Transaction.posting_datetime,
        Transaction.created_at,
    )
    source_statement = func.coalesce(Transaction.source_statement_id, "")
    source_page = func.coalesce(Transaction.source_page_number, -1)
    source_row = func.coalesce(Transaction.source_row_index, -1)
    is_date_only = func.coalesce(Transaction.timestamp_precision, "unknown").in_(
        ["date_only", "unknown"]
    )
    date_only_tiebreak_statement = case((is_date_only, source_statement), else_="")
    date_only_tiebreak_page = case((is_date_only, source_page), else_=-1)
    date_only_tiebreak_row = case((is_date_only, source_row), else_=-1)

    items = (
        query.order_by(
            event_datetime.desc().nullslast(),
            date_only_tiebreak_statement.desc(),
            date_only_tiebreak_page.desc(),
            date_only_tiebreak_row.desc(),
            Transaction.created_at.desc().nullslast(),
            Transaction.id.desc(),
        )
        .offset(offset)
        .limit(limit)
        .all()
    )
    return {"total": total, "items": [_serialize_transaction(item) for item in items]}


@router.post("/", response_model=TransactionOut)
def create_transaction(payload: TransactionCreate, db: Session = Depends(get_db)):
    item = Transaction(**payload.model_dump())
    db.add(item)
    db.commit()
    db.refresh(item)
    return _serialize_transaction(item)


@router.post("/{transaction_id}/approve-category", response_model=TransactionOut)
def approve_transaction_category(transaction_id: str, db: Session = Depends(get_db)):
    item = db.query(Transaction).filter(Transaction.id == transaction_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Transaction not found")

    item.category = _approved_category(item)
    item.review_status = ReviewStatus.reviewed.value
    db.commit()
    db.refresh(item)
    return _serialize_transaction(item)


@router.post("/{transaction_id}/mark-reviewed", response_model=TransactionOut)
def mark_transaction_reviewed(transaction_id: str, db: Session = Depends(get_db)):
    item = db.query(Transaction).filter(Transaction.id == transaction_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Transaction not found")

    item.review_status = ReviewStatus.reviewed.value
    db.commit()
    db.refresh(item)
    return _serialize_transaction(item)


@router.post("/{transaction_id}/mark-duplicate", response_model=TransactionOut)
def mark_transaction_duplicate(transaction_id: str, db: Session = Depends(get_db)):
    item = db.query(Transaction).filter(Transaction.id == transaction_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Transaction not found")

    tags = _normalize_tags(item.tags)
    if "duplicate" not in tags:
        tags.append("duplicate")
    item.tags = tags
    item.review_status = ReviewStatus.reviewed.value
    if not (item.category or "").strip():
        item.category = "Duplicate"
    db.commit()
    db.refresh(item)
    return _serialize_transaction(item)


@router.get("/{transaction_id}", response_model=TransactionOut)
def get_transaction(transaction_id: str, db: Session = Depends(get_db)):
    item = db.query(Transaction).filter(Transaction.id == transaction_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return _serialize_transaction(item)
