from __future__ import annotations

import hashlib
import logging
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Iterable, Optional, Sequence

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from ..db import SessionLocal
from ..models import (
    ExceptionItem,
    ImportBatch,
    ImportFile,
    ImportStatus,
    Statement,
    StatementRow,
    Transaction,
)
from ..domain import TxCandidate, dedupe, fingerprint, normalize_row
from .pdf_extract import extract_pdf_text
from .statement_parser import ParsedStatementBundle, parse_pdf_into_statements
from .net_worth import (
    ensure_reconciliation_exception,
    ensure_statement_balance_snapshots,
    get_or_create_account,
)
from .transfers import detect_transfer_links_in_session
from .rules_engine import apply_rules_to_transactions

logger = logging.getLogger(__name__)
DEDUPE_LOOKUP_CHUNK_SIZE = 500
MAX_IMPORT_ERROR_MESSAGE_LEN = 500
FUZZY_OVERLAP_DEDUPE_STATEMENT_BUFFER_DAYS = 3
FUZZY_OVERLAP_REFMATCH_MAX_DAYS = 2
FUZZY_OVERLAP_REFMATCH_MIN_SCORE = 0.9
FUZZY_OVERLAP_MERCHANT_MAX_DAYS = 1
FUZZY_OVERLAP_MERCHANT_MIN_DESC_SIM = 0.6
FUZZY_OVERLAP_NOREF_MAX_DAYS = 0
FUZZY_OVERLAP_NOREF_MAX_SECONDS = 3 * 60 * 60
FUZZY_OVERLAP_NOREF_MIN_DESC_SIM = 0.9
FUZZY_OVERLAP_REVIEW_MAX_DAYS = 2
FUZZY_OVERLAP_REVIEW_MIN_DESC_SIM = 0.5
FUZZY_OVERLAP_REVIEW_MAX_MATCHES = 5
FUZZY_OVERLAP_MIN_AUTO_GAP = 0.1

_SYNTHETIC_REFERENCE_RE = re.compile(r"^[0-9a-f]{24}$")


@contextmanager
def _session_scope() -> Iterable[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _as_float(value: Optional[Decimal]) -> Optional[float]:
    if value is None:
        return None
    return float(value)


def _iter_chunks(values: Sequence[str], chunk_size: int) -> Iterable[list[str]]:
    if chunk_size <= 0:
        yield list(values)
        return
    for idx in range(0, len(values), chunk_size):
        yield list(values[idx : idx + chunk_size])


def _sanitize_import_error(message: str) -> str:
    text = (message or "").strip()
    if not text:
        return "Unknown parsing error"
    if len(text) <= MAX_IMPORT_ERROR_MESSAGE_LEN:
        return text
    return f"{text[: MAX_IMPORT_ERROR_MESSAGE_LEN - 1]}…"


def _load_existing_transactions_by_dedup_key(
    db: Session,
    *,
    account_id: str,
    candidate_keys: Sequence[str],
) -> list[Transaction]:
    existing_transactions: list[Transaction] = []
    for key_chunk in _iter_chunks(candidate_keys, DEDUPE_LOOKUP_CHUNK_SIZE):
        if not key_chunk:
            continue
        existing_transactions.extend(
            db.query(Transaction)
            .filter(Transaction.account_id == account_id)
            .filter(Transaction.dedup_key.in_(key_chunk))
            .all()
        )
    return existing_transactions


def _build_reconciliation_payload(
    *,
    opening_balance: Optional[Decimal],
    closing_balance: Optional[Decimal],
    total_credits: Optional[Decimal],
    total_debits: Optional[Decimal],
    txs: list[tuple[int, object]],
) -> Optional[dict]:
    if opening_balance is None or closing_balance is None:
        return None

    credits: Decimal
    debits: Decimal
    method: str

    if total_credits is not None and total_debits is not None:
        credits = total_credits
        debits = total_debits
        method = "statement_totals"
    else:
        credits = Decimal("0")
        debits = Decimal("0")
        for _, tx in txs:
            amount = getattr(tx, "amount", None)
            direction = getattr(tx, "direction", None)
            if amount is None or direction is None:
                continue
            if direction == "in":
                credits += amount
            elif direction == "out":
                debits += amount
        method = "computed_from_parsed_transactions"

    expected = opening_balance + credits - debits
    delta = expected - closing_balance

    return {
        "opening_balance": float(opening_balance),
        "closing_balance": float(closing_balance),
        "credits": float(credits),
        "debits": float(debits),
        "expected_closing_balance": float(expected),
        "delta": float(delta),
        "method": method,
    }


def _normalize_candidate_text(value: str) -> str:
    return " ".join((value or "").strip().lower().split())


def _candidate_ts_key(value) -> str:
    if value is None:
        return ""
    return value.replace(microsecond=0).isoformat()


def _normalize_overlap_reference(value: str) -> str:
    return "".join(char for char in (value or "").strip().lower() if char.isalnum())


def _is_synthetic_overlap_reference(value: str) -> bool:
    trimmed = (value or "").strip().lower()
    return bool(trimmed) and bool(_SYNTHETIC_REFERENCE_RE.fullmatch(trimmed))


def _overlap_token_set(*values: str) -> set[str]:
    tokens: set[str] = set()
    for value in values:
        cleaned = "".join(char if char.isalnum() else " " for char in (value or "").lower())
        for token in cleaned.split():
            if len(token) < 3:
                continue
            if token.isdigit():
                continue
            tokens.add(token)
    return tokens


def _token_similarity(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    intersection = left & right
    union = left | right
    if not union:
        return 0.0
    min_size = min(len(left), len(right))
    if min_size < 2:
        return len(intersection) / len(union)
    return len(intersection) / min_size


def _as_naive_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=None)
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _anchor_datetimes(*values: Optional[datetime]) -> list[datetime]:
    anchors: list[datetime] = []
    for value in values:
        if value is None:
            continue
        anchors.append(_as_naive_utc(value).replace(microsecond=0))
    return anchors


def _min_anchor_deltas(
    *,
    candidate_operation: Optional[datetime],
    candidate_posting: Optional[datetime],
    existing_operation: Optional[datetime],
    existing_posting: Optional[datetime],
) -> tuple[int, float]:
    candidate_anchors = _anchor_datetimes(candidate_operation, candidate_posting)
    existing_anchors = _anchor_datetimes(existing_operation, existing_posting)
    if not candidate_anchors or not existing_anchors:
        return 999, float("inf")

    min_days = 999
    min_seconds = float("inf")
    for cand in candidate_anchors:
        for existing in existing_anchors:
            days = abs((cand.date() - existing.date()).days)
            seconds = abs((cand - existing).total_seconds())
            min_days = min(min_days, days)
            min_seconds = min(min_seconds, seconds)
    return min_days, min_seconds


def _amount_cents(value: object) -> int:
    amount = Decimal(str(value or "0")).quantize(Decimal("0.01"))
    return int(abs(amount) * 100)


@dataclass(frozen=True)
class _OverlapTxCandidate:
    id: str
    dedup_key: str
    amount_cents: int
    currency: str
    direction: str
    operation_datetime: Optional[datetime]
    posting_datetime: Optional[datetime]
    reference_norm: str
    merchant_norm: str
    tokens: set[str]
    timestamp_precision: str


def _ref_match_kind(*, cand_ref_norm: str, existing_ref_norm: str) -> str:
    if not cand_ref_norm or not existing_ref_norm:
        return "none"
    if cand_ref_norm == existing_ref_norm:
        return "match"
    if len(cand_ref_norm) >= 6 and len(existing_ref_norm) >= 6:
        if cand_ref_norm in existing_ref_norm or existing_ref_norm in cand_ref_norm:
            return "partial"
    return "mismatch"


def _max_seconds_for_precision(*, candidate_precision: str, existing_precision: str) -> int:
    if candidate_precision == "date_only" or existing_precision == "date_only":
        return 36 * 60 * 60
    return FUZZY_OVERLAP_NOREF_MAX_SECONDS


def _load_existing_transactions_for_overlap_dedupe(
    db: Session,
    *,
    account_id: str,
    period_start: Optional[datetime],
    period_end: Optional[datetime],
) -> list[Transaction]:
    query = db.query(Transaction).filter(Transaction.account_id == account_id)

    if period_start is None and period_end is None:
        return query.all()

    start = (
        period_start - timedelta(days=FUZZY_OVERLAP_DEDUPE_STATEMENT_BUFFER_DAYS)
        if period_start is not None
        else None
    )
    end = (
        period_end + timedelta(days=FUZZY_OVERLAP_DEDUPE_STATEMENT_BUFFER_DAYS)
        if period_end is not None
        else None
    )

    if start is not None and end is not None:
        query = query.filter(
            or_(
                and_(
                    Transaction.operation_datetime >= start, Transaction.operation_datetime <= end
                ),
                and_(Transaction.posting_datetime >= start, Transaction.posting_datetime <= end),
            )
        )
    elif start is not None:
        query = query.filter(
            or_(
                Transaction.operation_datetime >= start,
                Transaction.posting_datetime >= start,
            )
        )
    elif end is not None:
        query = query.filter(
            or_(
                Transaction.operation_datetime <= end,
                Transaction.posting_datetime <= end,
            )
        )

    return query.all()


def _index_overlap_candidates(
    existing: Sequence[Transaction],
) -> dict[tuple[int, str, str], list[_OverlapTxCandidate]]:
    indexed: dict[tuple[int, str, str], list[_OverlapTxCandidate]] = {}
    for tx in existing:
        amount_cents = _amount_cents(tx.amount)
        currency = (tx.currency or "RUB").strip() or "RUB"
        direction = (tx.direction or "out").strip() or "out"
        signature = (amount_cents, currency, direction)
        reference_norm = (
            ""
            if _is_synthetic_overlap_reference(tx.bank_reference_id)
            else _normalize_overlap_reference(tx.bank_reference_id)
        )
        indexed.setdefault(signature, []).append(
            _OverlapTxCandidate(
                id=tx.id,
                dedup_key=tx.dedup_key or "",
                amount_cents=amount_cents,
                currency=currency,
                direction=direction,
                operation_datetime=tx.operation_datetime,
                posting_datetime=tx.posting_datetime,
                reference_norm=reference_norm,
                merchant_norm=_normalize_candidate_text(tx.merchant_normalized),
                tokens=_overlap_token_set(tx.description_raw, tx.merchant_normalized),
                timestamp_precision=(tx.timestamp_precision or "unknown").strip() or "unknown",
            )
        )
    return indexed


def _plan_fuzzy_overlap_dedupe(
    candidates: Sequence[TxCandidate],
    *,
    existing_by_key: dict[str, str],
    existing_candidates: dict[tuple[int, str, str], list[_OverlapTxCandidate]],
) -> tuple[list[TxCandidate], dict[str, str], dict[str, dict]]:
    auto_by_key: dict[str, str] = {}
    review_by_key: dict[str, dict] = {}
    replacements: dict[str, TxCandidate] = {}

    for candidate in candidates:
        dedup_key = fingerprint(candidate)
        if dedup_key in existing_by_key:
            continue

        signature = (
            _amount_cents(candidate.amount),
            (candidate.currency or "RUB").strip() or "RUB",
            (candidate.direction or "out").strip() or "out",
        )
        pool = existing_candidates.get(signature) or []
        if not pool:
            continue

        cand_ref_norm = (
            ""
            if _is_synthetic_overlap_reference(candidate.bank_reference_id)
            else _normalize_overlap_reference(candidate.bank_reference_id)
        )
        cand_merchant_norm = _normalize_candidate_text(candidate.merchant_normalized)
        cand_tokens = _overlap_token_set(candidate.description_raw, candidate.merchant_normalized)
        cand_precision = (candidate.timestamp_precision or "unknown").strip() or "unknown"

        scored: list[tuple[float, _OverlapTxCandidate, dict]] = []
        auto_scored: list[tuple[float, _OverlapTxCandidate, dict]] = []
        for existing in pool:
            delta_days, delta_seconds = _min_anchor_deltas(
                candidate_operation=candidate.operation_datetime,
                candidate_posting=candidate.posting_datetime,
                existing_operation=existing.operation_datetime,
                existing_posting=existing.posting_datetime,
            )

            ref_kind = _ref_match_kind(
                cand_ref_norm=cand_ref_norm, existing_ref_norm=existing.reference_norm
            )
            merchant_match = (
                bool(cand_merchant_norm)
                and bool(existing.merchant_norm)
                and cand_merchant_norm == existing.merchant_norm
            )

            desc_sim = _token_similarity(cand_tokens, existing.tokens)

            score = desc_sim
            if ref_kind == "match":
                score = 1.0 - min(delta_days, FUZZY_OVERLAP_REFMATCH_MAX_DAYS) * 0.05
            elif merchant_match and score:
                score = min(1.0, score + 0.05)

            details = {
                "score": round(score, 4),
                "ref_kind": ref_kind,
                "merchant_match": merchant_match,
                "delta_days": int(delta_days),
                "delta_seconds": int(delta_seconds) if delta_seconds != float("inf") else None,
                "desc_sim": round(desc_sim, 4),
            }

            # Review candidates: broad enough to avoid silent overlaps.
            if delta_days <= FUZZY_OVERLAP_REVIEW_MAX_DAYS and (
                ref_kind in {"match", "partial"}
                or (merchant_match and desc_sim >= FUZZY_OVERLAP_REVIEW_MIN_DESC_SIM)
                or desc_sim >= FUZZY_OVERLAP_REVIEW_MIN_DESC_SIM
            ):
                scored.append((score, existing, details))

            # Auto candidates: strict enough to avoid false merges.
            max_seconds = _max_seconds_for_precision(
                candidate_precision=cand_precision,
                existing_precision=existing.timestamp_precision,
            )
            if ref_kind == "match" and delta_days <= FUZZY_OVERLAP_REFMATCH_MAX_DAYS:
                auto_scored.append((score, existing, details))
                continue

            if (
                merchant_match
                and delta_days <= FUZZY_OVERLAP_MERCHANT_MAX_DAYS
                and ref_kind != "mismatch"
            ):
                if desc_sim >= FUZZY_OVERLAP_MERCHANT_MIN_DESC_SIM and (
                    delta_days > 0 or delta_seconds <= max_seconds
                ):
                    auto_scored.append((score, existing, details))
                continue

            if (
                delta_days <= FUZZY_OVERLAP_NOREF_MAX_DAYS
                and delta_seconds <= max_seconds
                and desc_sim >= FUZZY_OVERLAP_NOREF_MIN_DESC_SIM
                and ref_kind != "mismatch"
            ):
                auto_scored.append((score, existing, details))

        if auto_scored:
            auto_scored.sort(key=lambda item: item[0], reverse=True)
            best_score, best_existing, best_details = auto_scored[0]
            second_score = auto_scored[1][0] if len(auto_scored) > 1 else 0.0
            gap = best_score - second_score

            if (
                best_details.get("ref_kind") == "match"
                and best_score >= FUZZY_OVERLAP_REFMATCH_MIN_SCORE
                and gap >= FUZZY_OVERLAP_MIN_AUTO_GAP
            ):
                auto_by_key[dedup_key] = best_existing.id
                continue

            if best_details.get("merchant_match") and gap >= FUZZY_OVERLAP_MIN_AUTO_GAP:
                auto_by_key[dedup_key] = best_existing.id
                continue

            if gap >= FUZZY_OVERLAP_MIN_AUTO_GAP:
                auto_by_key[dedup_key] = best_existing.id
                continue

        if not scored:
            continue

        scored.sort(key=lambda item: item[0], reverse=True)
        review_matches: list[dict] = []
        for score, existing, details in scored[:FUZZY_OVERLAP_REVIEW_MAX_MATCHES]:
            review_matches.append(
                {
                    "transaction_id": existing.id,
                    "dedup_key": existing.dedup_key,
                    "score": details.get("score"),
                    "ref_kind": details.get("ref_kind"),
                    "merchant_match": details.get("merchant_match"),
                    "delta_days": details.get("delta_days"),
                    "delta_seconds": details.get("delta_seconds"),
                    "desc_sim": details.get("desc_sim"),
                }
            )

        review_by_key[dedup_key] = {
            "reason": "ambiguous_fuzzy_overlap_match",
            "candidate_statement_row_id": candidate.statement_row_id,
            "candidate_statement_id": candidate.source_statement_id,
            "candidate_bank_reference_id": candidate.bank_reference_id,
            "candidate_description_raw": candidate.description_raw,
            "candidate_merchant_normalized": candidate.merchant_normalized,
            "candidate_amount": str(candidate.amount.quantize(Decimal("0.01"))),
            "candidate_currency": candidate.currency,
            "candidate_direction": candidate.direction,
            "candidate_operation_datetime": _candidate_ts_key(candidate.operation_datetime),
            "candidate_posting_datetime": _candidate_ts_key(candidate.posting_datetime),
            "matches": review_matches,
        }
        replacements[candidate.statement_row_id] = replace(candidate, review_status="needs_review")

    updated_candidates = [replacements.get(item.statement_row_id, item) for item in candidates]
    return updated_candidates, auto_by_key, review_by_key


def _inject_synthetic_references_for_statement_duplicates(
    candidates: Sequence[TxCandidate], *, statement_id: str
) -> list[TxCandidate]:
    grouped: dict[tuple[str, ...], list[TxCandidate]] = {}
    for candidate in candidates:
        if (candidate.bank_reference_id or "").strip():
            continue
        group_key = (
            candidate.account_id,
            candidate.currency or "RUB",
            candidate.direction or "out",
            f"{candidate.amount.quantize(Decimal('0.01'))}",
            _candidate_ts_key(candidate.operation_datetime),
            _candidate_ts_key(candidate.posting_datetime),
            _normalize_candidate_text(candidate.description_raw),
            _normalize_candidate_text(candidate.bank_category),
            _normalize_candidate_text(candidate.raw_text),
        )
        grouped.setdefault(group_key, []).append(candidate)

    replacements: dict[str, TxCandidate] = {}
    for group_key, group in grouped.items():
        if len(group) <= 1:
            continue
        for occurrence, candidate in enumerate(
            sorted(group, key=lambda item: item.statement_row_id), start=1
        ):
            payload = "|".join(("stmtdup", statement_id, *group_key, str(occurrence)))
            synthetic_ref = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:24]
            replacements[candidate.statement_row_id] = replace(
                candidate, bank_reference_id=synthetic_ref
            )

    return [replacements.get(candidate.statement_row_id, candidate) for candidate in candidates]


def _persist_statement_bundle(
    db: Session,
    *,
    import_file: ImportFile,
    bundle: ParsedStatementBundle,
) -> list[Transaction]:
    meta = bundle.meta
    rows = bundle.rows
    txs = bundle.txs

    account = get_or_create_account(
        db,
        provider=meta.provider,
        statement_type=meta.statement_type,
        account_display=meta.account_display,
        currency=meta.currency,
    )

    statement = Statement(
        provider=meta.provider,
        account_id=account.id,
        account_display=meta.account_display,
        statement_type=meta.statement_type,
        period_start=meta.period_start,
        period_end=meta.period_end,
        generated_at=meta.generated_at,
        currency=meta.currency,
        opening_balance=_as_float(meta.opening_balance),
        closing_balance=_as_float(meta.closing_balance),
        total_credits=_as_float(meta.total_credits),
        total_debits=_as_float(meta.total_debits),
        parse_confidence=_as_float(meta.parse_confidence),
        reconcile_status=meta.reconcile_status,
        pdf_path=import_file.file_path,
    )
    db.add(statement)
    db.flush()

    ensure_statement_balance_snapshots(db, statement=statement)
    ensure_reconciliation_exception(
        db,
        statement=statement,
        payload=_build_reconciliation_payload(
            opening_balance=meta.opening_balance,
            closing_balance=meta.closing_balance,
            total_credits=meta.total_credits,
            total_debits=meta.total_debits,
            txs=txs,
        ),
    )

    row_by_index: dict[int, StatementRow] = {}
    parsed_row_by_index = {row.row_index: row for row in rows}
    for parsed_row in rows:
        row = StatementRow(
            statement_id=statement.id,
            row_index=parsed_row.row_index,
            page_number=parsed_row.page_number,
            raw_text=parsed_row.raw_text,
            raw_data=parsed_row.raw_data,
            amount=_as_float(parsed_row.amount),
            currency=parsed_row.currency,
            direction=parsed_row.direction,
            operation_date=parsed_row.operation_date,
            posting_date=parsed_row.posting_date,
            parse_confidence=_as_float(parsed_row.parse_confidence),
        )
        db.add(row)
        row_by_index[parsed_row.row_index] = row
    db.flush()

    candidates = []
    for row_index, parsed_tx in txs:
        row = row_by_index.get(row_index)
        parsed_row = parsed_row_by_index.get(row_index)
        if row is None or parsed_row is None:
            continue
        candidates.append(
            normalize_row(
                row=parsed_row,
                tx=parsed_tx,
                account_id=account.id,
                statement_row_id=row.id,
                statement_id=statement.id,
            )
        )
    candidates = _inject_synthetic_references_for_statement_duplicates(
        candidates, statement_id=statement.id
    )

    candidate_keys = sorted({fingerprint(candidate) for candidate in candidates})
    existing_transactions = _load_existing_transactions_by_dedup_key(
        db,
        account_id=account.id,
        candidate_keys=candidate_keys,
    )
    existing_by_key = {tx.dedup_key: tx.id for tx in existing_transactions if tx.dedup_key}
    overlap_existing_transactions = _load_existing_transactions_for_overlap_dedupe(
        db,
        account_id=account.id,
        period_start=meta.period_start,
        period_end=meta.period_end,
    )
    existing_obj_by_id = {tx.id: tx for tx in overlap_existing_transactions}
    existing_obj_by_id.update({tx.id: tx for tx in existing_transactions})

    candidates, fuzzy_by_key, fuzzy_review_by_key = _plan_fuzzy_overlap_dedupe(
        candidates,
        existing_by_key=existing_by_key,
        existing_candidates=_index_overlap_candidates(overlap_existing_transactions),
    )
    existing_by_key.update(fuzzy_by_key)

    dedupe_result = dedupe(candidates, existing_by_key)

    created_transactions: list[Transaction] = []
    created_by_key: dict[str, Transaction] = {}
    for draft in dedupe_result.canonical_transactions:
        item = Transaction(
            account_id=draft.account_id,
            dedup_key=draft.dedup_key,
            operation_datetime=draft.operation_datetime,
            posting_datetime=draft.posting_datetime,
            amount=_as_float(draft.amount) or 0.0,
            currency=draft.currency,
            direction=draft.direction,
            description_raw=draft.description_raw,
            merchant_normalized=draft.merchant_normalized,
            bank_reference_id=draft.bank_reference_id,
            bank_category=draft.bank_category,
            meaning=draft.meaning,
            meaning_confidence=_as_float(draft.meaning_confidence),
            category=draft.category,
            tags=draft.tags,
            review_status=draft.review_status,
            timestamp_precision=draft.timestamp_precision,
            source_statement_id=draft.source_statement_id,
            source_page_number=draft.source_page_number,
            source_row_index=draft.source_row_index,
        )
        db.add(item)
        db.flush()
        created_transactions.append(item)
        created_by_key[draft.dedup_key] = item

    row_by_id = {row.id: row for row in row_by_index.values()}
    for link in dedupe_result.statement_row_links:
        row = row_by_id.get(link.statement_row_id)
        if row is None:
            continue
        tx_obj = None
        if link.transaction_id:
            tx_obj = existing_obj_by_id.get(link.transaction_id)
            if tx_obj is None:
                tx_obj = db.query(Transaction).filter(Transaction.id == link.transaction_id).first()
        if tx_obj is None:
            tx_obj = created_by_key.get(link.dedup_key)
        if tx_obj is None:
            continue
        tx_obj.statement_rows.append(row)

    if fuzzy_review_by_key:
        for planned_key, payload in fuzzy_review_by_key.items():
            tx_obj = created_by_key.get(planned_key)
            if tx_obj is None:
                continue
            db.add(
                ExceptionItem(
                    exception_type="suspected_overlap_duplicate",
                    severity="medium",
                    status="open",
                    entity_type="transaction",
                    entity_id=tx_obj.id,
                    rationale="Potential duplicate transaction detected across overlapping statements",
                    payload=payload,
                )
            )

    return created_transactions


def persist_parsed_bundles(
    db: Session,
    *,
    import_file: ImportFile,
    bundles: Sequence[ParsedStatementBundle],
) -> list[Transaction]:
    created_transactions: list[Transaction] = []
    for bundle in bundles:
        created_transactions.extend(
            _persist_statement_bundle(db, import_file=import_file, bundle=bundle)
        )
    return created_transactions


def process_import_batch(batch_id: str) -> None:
    with _session_scope() as db:
        batch = db.query(ImportBatch).filter(ImportBatch.id == batch_id).first()
        if not batch:
            logger.warning("Import batch '%s' not found.", batch_id)
            return

        queued_files = (
            db.query(ImportFile)
            .filter(ImportFile.batch_id == batch_id)
            .filter(ImportFile.status == ImportStatus.queued.value)
            .order_by(ImportFile.created_at.asc())
            .all()
        )
        if not queued_files:
            logger.info("Import batch '%s' has no queued files.", batch_id)
            return

        started_at = time.monotonic()
        logger.info(
            "Processing import batch '%s' with %d queued file(s).",
            batch_id,
            len(queued_files),
        )
        for import_file in queued_files:
            process_import_file(import_file_id=import_file.id, db=db)

        # Derive batch status from file statuses.
        statuses = [
            f.status for f in db.query(ImportFile).filter(ImportFile.batch_id == batch_id).all()
        ]
        if any(s == ImportStatus.failed.value for s in statuses):
            batch.status = ImportStatus.failed.value
        elif any(s in {ImportStatus.queued.value, ImportStatus.processing.value} for s in statuses):
            batch.status = ImportStatus.queued.value
        elif statuses:
            batch.status = ImportStatus.processed.value
        db.commit()
        logger.info(
            "Completed import batch '%s' with status '%s' in %.2fs.",
            batch_id,
            batch.status,
            time.monotonic() - started_at,
        )

        # Best-effort linking of internal transfers after new transactions land.
        try:
            detection_result = detect_transfer_links_in_session(db)
            db.commit()
            logger.info(
                "Transfer relink for batch '%s': created=%d marked_internal=%d.",
                batch_id,
                detection_result.links_created,
                detection_result.transactions_marked_internal,
            )
        except Exception:
            db.rollback()
            logger.warning(
                "Transfer relink failed after processing import batch '%s'.",
                batch_id,
                exc_info=True,
            )


def process_all_queued_imports(*, max_batches: int = 50) -> None:
    with _session_scope() as db:
        batches = (
            db.query(ImportBatch)
            .filter(ImportBatch.status == ImportStatus.queued.value)
            .order_by(ImportBatch.created_at.asc())
            .limit(max_batches)
            .all()
        )
        for batch in batches:
            process_import_batch(batch.id)


def process_import_file(*, import_file_id: str, db: Optional[Session] = None) -> None:
    owns_session = db is None
    if owns_session:
        with _session_scope() as session:
            process_import_file(import_file_id=import_file_id, db=session)
        return

    assert db is not None

    import_file = db.query(ImportFile).filter(ImportFile.id == import_file_id).first()
    if not import_file:
        logger.warning("Import file '%s' not found.", import_file_id)
        return

    if import_file.status in {ImportStatus.processing.value, ImportStatus.processed.value}:
        logger.info(
            "Skipping import file '%s' with status '%s'.",
            import_file.id,
            import_file.status,
        )
        return
    if import_file.status in {"duplicate", "failed"}:
        logger.info(
            "Skipping import file '%s' with terminal status '%s'.",
            import_file.id,
            import_file.status,
        )
        return

    import_file.status = ImportStatus.processing.value
    db.commit()
    logger.info(
        "Started processing import file '%s' (%s).",
        import_file.id,
        import_file.file_name,
    )

    started_at = time.monotonic()
    try:
        pdf_text = extract_pdf_text(import_file.file_path)
        bundles = parse_pdf_into_statements(
            pdf_text=pdf_text, file_name=import_file.file_name, pdf_path=import_file.file_path
        )

        created_transactions = persist_parsed_bundles(db, import_file=import_file, bundles=bundles)

        if created_transactions:
            apply_rules_to_transactions(db, transactions=created_transactions)

        import_file.status = ImportStatus.processed.value
        db.commit()
        logger.info(
            "Processed import file '%s': bundles=%d created_transactions=%d in %.2fs.",
            import_file.id,
            len(bundles),
            len(created_transactions),
            time.monotonic() - started_at,
        )
    except Exception as exc:
        # Ensure we can continue using the session after failed flush/commit.
        db.rollback()
        error_text = _sanitize_import_error(str(exc))
        logger.warning(
            "Failed to parse import file '%s' (%s): %s",
            import_file.id,
            import_file.file_name,
            error_text,
            exc_info=True,
        )
        import_file.status = ImportStatus.failed.value
        import_file.error_message = _sanitize_import_error(f"Parsing failed: {exc}")
        db.add(
            ExceptionItem(
                exception_type="parsing_anomaly",
                severity="high",
                status="open",
                entity_type="import_file",
                entity_id=import_file.id,
                rationale="Failed to parse PDF into statement/transactions",
                payload={
                    "error": error_text,
                    "error_type": exc.__class__.__name__,
                    "file_name": import_file.file_name,
                },
            )
        )
        db.commit()
