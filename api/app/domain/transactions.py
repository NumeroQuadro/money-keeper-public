from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Mapping, Optional, Sequence


_WHITESPACE_RE = re.compile(r"\s+")
_FINGERPRINT_VERSION = "v1"


@dataclass(frozen=True)
class TxCandidate:
    account_id: str
    statement_row_id: str
    operation_datetime: Optional[datetime]
    posting_datetime: Optional[datetime]
    amount: Decimal
    currency: str
    direction: str
    description_raw: str
    merchant_normalized: str
    bank_reference_id: str
    bank_category: str
    meaning: str
    meaning_confidence: Optional[Decimal]
    category: str
    tags: Optional[list[str]]
    review_status: str
    raw_text: str
    timestamp_precision: str = "unknown"
    source_statement_id: str = ""
    source_page_number: int = 0
    source_row_index: int = 0


@dataclass(frozen=True)
class CanonicalTransactionDraft:
    account_id: str
    dedup_key: str
    operation_datetime: Optional[datetime]
    posting_datetime: Optional[datetime]
    amount: Decimal
    currency: str
    direction: str
    description_raw: str
    merchant_normalized: str
    bank_reference_id: str
    bank_category: str
    meaning: str
    meaning_confidence: Optional[Decimal]
    category: str
    tags: Optional[list[str]]
    review_status: str
    timestamp_precision: str = "unknown"
    source_statement_id: str = ""
    source_page_number: int = 0
    source_row_index: int = 0


@dataclass(frozen=True)
class StatementRowLinkDraft:
    statement_row_id: str
    dedup_key: str
    transaction_id: Optional[str] = None


@dataclass(frozen=True)
class DedupeResult:
    canonical_transactions: list[CanonicalTransactionDraft]
    statement_row_links: list[StatementRowLinkDraft]
    deduped_existing_count: int


def _normalize_text(value: str) -> str:
    lowered = (value or "").strip().lower()
    return _WHITESPACE_RE.sub(" ", lowered)


def _timestamp_key(value: Optional[datetime]) -> str:
    if value is None:
        return ""
    return value.replace(microsecond=0).isoformat()


def _amount_cents(value: Decimal) -> int:
    quantized = value.quantize(Decimal("0.01"))
    return int(quantized * 100)


def normalize_row(
    *,
    row: Any,
    tx: Any,
    account_id: str,
    statement_row_id: str,
    statement_id: str = "",
) -> TxCandidate:
    operation_datetime = tx.operation_datetime or row.operation_date
    posting_datetime = tx.posting_datetime or row.posting_date
    amount = tx.amount or row.amount or Decimal("0")
    timestamp_precision = (
        getattr(tx, "timestamp_precision", None)
        or getattr(row, "timestamp_precision", None)
        or "unknown"
    )
    return TxCandidate(
        account_id=account_id,
        statement_row_id=statement_row_id,
        operation_datetime=operation_datetime,
        posting_datetime=posting_datetime,
        amount=amount,
        currency=tx.currency or row.currency or "RUB",
        direction=tx.direction or row.direction or "out",
        description_raw=tx.description_raw or row.raw_text or "",
        merchant_normalized=tx.merchant_normalized or "",
        bank_reference_id=tx.bank_reference_id or "",
        bank_category=tx.bank_category or "",
        meaning=tx.meaning or "unknown",
        meaning_confidence=tx.meaning_confidence,
        category=tx.category or "",
        tags=tx.tags,
        review_status="reviewed",
        raw_text=row.raw_text or "",
        timestamp_precision=timestamp_precision,
        source_statement_id=statement_id,
        source_page_number=getattr(row, "page_number", 0) or 0,
        source_row_index=getattr(row, "row_index", 0) or 0,
    )


def fingerprint(candidate: TxCandidate) -> str:
    amount_cents = _amount_cents(candidate.amount)
    reference = _normalize_text(candidate.bank_reference_id)
    components = [
        _FINGERPRINT_VERSION,
        candidate.account_id,
        candidate.currency or "RUB",
        candidate.direction or "out",
        str(amount_cents),
        _timestamp_key(candidate.operation_datetime),
        _timestamp_key(candidate.posting_datetime),
        reference,
        _normalize_text(candidate.description_raw),
        _normalize_text(candidate.bank_category),
        _normalize_text(candidate.raw_text),
    ]
    payload = "|".join(components)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def dedupe(candidates: Sequence[TxCandidate], existing: Mapping[str, str]) -> DedupeResult:
    canonical_transactions: list[CanonicalTransactionDraft] = []
    statement_row_links: list[StatementRowLinkDraft] = []

    known_by_key: dict[str, str] = dict(existing)
    planned_keys: set[str] = set()
    deduped_existing_count = 0

    for candidate in candidates:
        dedup_key = fingerprint(candidate)
        existing_transaction_id = known_by_key.get(dedup_key)
        if existing_transaction_id:
            statement_row_links.append(
                StatementRowLinkDraft(
                    statement_row_id=candidate.statement_row_id,
                    dedup_key=dedup_key,
                    transaction_id=existing_transaction_id,
                )
            )
            deduped_existing_count += 1
            continue

        if dedup_key not in planned_keys:
            canonical_transactions.append(
                CanonicalTransactionDraft(
                    account_id=candidate.account_id,
                    dedup_key=dedup_key,
                    operation_datetime=candidate.operation_datetime,
                    posting_datetime=candidate.posting_datetime,
                    amount=candidate.amount,
                    currency=candidate.currency,
                    direction=candidate.direction,
                    description_raw=candidate.description_raw,
                    merchant_normalized=candidate.merchant_normalized,
                    bank_reference_id=candidate.bank_reference_id,
                    bank_category=candidate.bank_category,
                    meaning=candidate.meaning,
                    meaning_confidence=candidate.meaning_confidence,
                    category=candidate.category,
                    tags=candidate.tags,
                    review_status=candidate.review_status,
                    timestamp_precision=candidate.timestamp_precision,
                    source_statement_id=candidate.source_statement_id,
                    source_page_number=candidate.source_page_number,
                    source_row_index=candidate.source_row_index,
                )
            )
            planned_keys.add(dedup_key)
            known_by_key[dedup_key] = ""

        statement_row_links.append(
            StatementRowLinkDraft(statement_row_id=candidate.statement_row_id, dedup_key=dedup_key)
        )

    return DedupeResult(
        canonical_transactions=canonical_transactions,
        statement_row_links=statement_row_links,
        deduped_existing_count=deduped_existing_count,
    )
