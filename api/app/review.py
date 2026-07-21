from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import and_, false, func, or_
from sqlalchemy.sql.elements import ColumnElement

from .models import Transaction

REVIEW_REASON_UNCATEGORIZED = "uncategorized_needs_review"
REVIEW_REASONS = (REVIEW_REASON_UNCATEGORIZED,)

_OBVIOUS_OUTFLOW_MEANINGS = ("spend", "fee")
_OBVIOUS_INFLOW_MEANINGS = ("income", "refund", "cashback", "interest")
_TRANSFER_MEANINGS = ("internal_transfer", "external_transfer")
_TRANSFER_BANK_CATEGORY = "transfer"


def _normalized_text_expr(column: object) -> ColumnElement[str]:
    return func.lower(func.trim(func.coalesce(column, "")))


def category_is_empty_expr() -> ColumnElement[bool]:
    return func.length(func.trim(func.coalesce(Transaction.category, ""))) == 0


def uncategorized_review_expr() -> ColumnElement[bool]:
    category_empty = category_is_empty_expr()
    direction = _normalized_text_expr(Transaction.direction)
    meaning = _normalized_text_expr(Transaction.meaning)
    merchant = _normalized_text_expr(Transaction.merchant_normalized)
    bank_category = _normalized_text_expr(Transaction.bank_category)

    merchant_present = func.length(merchant) > 0
    bank_category_present = func.length(bank_category) > 0
    transfer_like = or_(
        meaning.in_(_TRANSFER_MEANINGS),
        bank_category == _TRANSFER_BANK_CATEGORY,
    )
    obvious_merchant_outflow = and_(
        direction == "out",
        merchant_present,
        bank_category != _TRANSFER_BANK_CATEGORY,
    )
    obvious_outflow = and_(
        direction == "out",
        meaning.in_(_OBVIOUS_OUTFLOW_MEANINGS),
        or_(
            merchant_present,
            and_(bank_category_present, bank_category != _TRANSFER_BANK_CATEGORY),
        ),
    )
    obvious_inflow = and_(
        direction == "in",
        meaning.in_(_OBVIOUS_INFLOW_MEANINGS),
        or_(
            merchant_present,
            and_(bank_category_present, bank_category != _TRANSFER_BANK_CATEGORY),
        ),
    )

    return and_(
        category_empty,
        ~transfer_like,
        ~obvious_merchant_outflow,
        ~obvious_outflow,
        ~obvious_inflow,
    )


def transaction_review_expr(review_reason: str | None = None) -> ColumnElement[bool]:
    normalized_reason = (review_reason or "").strip().lower()
    if normalized_reason and normalized_reason != REVIEW_REASON_UNCATEGORIZED:
        return false()
    return uncategorized_review_expr()


def compute_transaction_review_reasons(tx: Transaction) -> list[str]:
    category = (tx.category or "").strip()
    if category:
        return []

    direction = (tx.direction or "").strip().lower()
    meaning = (tx.meaning or "unknown").strip().lower()
    merchant = (tx.merchant_normalized or "").strip()
    bank_category = (tx.bank_category or "").strip().lower()
    bank_category_present = bool(bank_category)

    transfer_like = meaning in _TRANSFER_MEANINGS or bank_category == _TRANSFER_BANK_CATEGORY
    obvious_merchant_outflow = (
        direction == "out" and bool(merchant) and bank_category != _TRANSFER_BANK_CATEGORY
    )
    obvious_outflow = (
        direction == "out"
        and meaning in _OBVIOUS_OUTFLOW_MEANINGS
        and (bool(merchant) or (bank_category_present and bank_category != _TRANSFER_BANK_CATEGORY))
    )
    obvious_inflow = (
        direction == "in"
        and meaning in _OBVIOUS_INFLOW_MEANINGS
        and (bool(merchant) or (bank_category_present and bank_category != _TRANSFER_BANK_CATEGORY))
    )

    if transfer_like or obvious_merchant_outflow or obvious_outflow or obvious_inflow:
        return []

    return [REVIEW_REASON_UNCATEGORIZED]


def needs_human_review(tx: Transaction | None) -> bool:
    if tx is None:
        return False
    return bool(compute_transaction_review_reasons(tx))


def normalize_review_reasons(value: Sequence[str] | None) -> list[str]:
    if not value:
        return []

    normalized: list[str] = []
    for item in value:
        reason = str(item).strip().lower()
        if not reason or reason in normalized:
            continue
        normalized.append(reason)
    return normalized
