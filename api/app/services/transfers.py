from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from ..domain import TransferLanePrior, TransferTx, score_transfer_pair, select_transfer_links
from ..models import (
    Statement,
    StatementRow,
    Transaction,
    TransactionMeaning,
    TransferLink,
    TransferStatus,
    transaction_statement_link,
)


_LANE_PATTERN_TOKEN_RE = re.compile(r"[0-9a-zа-я]{3,}", re.IGNORECASE)
_LANE_PATTERN_STOPWORDS = {
    "перевод",
    "сбп",
    "sbp",
    "transfer",
    "between",
    "accounts",
    "между",
    "своими",
    "счет",
    "счета",
    "счетами",
    "списание",
    "зачисление",
    "incoming",
    "outgoing",
    "payment",
    "card",
    "from",
    "to",
}


@dataclass(frozen=True)
class TransferDetectionResult:
    links_created: int
    auto_links_created: int
    suggested_links_created: int
    transactions_marked_internal: int


def _amount_to_cents(value: object) -> Optional[int]:
    if value is None:
        return None
    try:
        dec = Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None
    return int(dec * 100)


def _tx_timestamp(row: object) -> Optional[datetime]:
    for attr in ("operation_datetime", "posting_datetime", "created_at"):
        timestamp = getattr(row, attr, None)
        if isinstance(timestamp, datetime):
            return timestamp
    return None


def _select_timestamp(*timestamps: object) -> Optional[datetime]:
    for timestamp in timestamps:
        if isinstance(timestamp, datetime):
            return timestamp
    return None


def _percentile_seconds(values: list[float], *, percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = int((len(ordered) - 1) * percentile)
    return float(ordered[idx])


def _extract_lane_patterns(
    texts: list[str],
    *,
    min_occurrences: int = 2,
    max_patterns: int = 8,
) -> tuple[str, ...]:
    counter: Counter[str] = Counter()
    for text in texts:
        if not text:
            continue
        for token in _LANE_PATTERN_TOKEN_RE.findall(text.lower()):
            if token in _LANE_PATTERN_STOPWORDS:
                continue
            counter[token] += 1

    if not counter:
        return tuple()

    ranked = sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    strong = [token for token, count in ranked if count >= min_occurrences]
    if not strong:
        strong = [token for token, _ in ranked[:3]]
    return tuple(strong[:max_patterns])


def _build_lane_priors(
    db: Session,
    *,
    tx_accounts_subquery,
) -> dict[tuple[str, str], TransferLanePrior]:
    tx_out = aliased(Transaction)
    tx_in = aliased(Transaction)
    out_accounts = tx_accounts_subquery.alias("out_accounts")
    in_accounts = tx_accounts_subquery.alias("in_accounts")

    rows = db.execute(
        select(
            func.coalesce(tx_out.account_id, out_accounts.c.account_id).label("out_account_id"),
            func.coalesce(tx_in.account_id, in_accounts.c.account_id).label("in_account_id"),
            tx_out.operation_datetime.label("out_operation_datetime"),
            tx_out.posting_datetime.label("out_posting_datetime"),
            tx_out.created_at.label("out_created_at"),
            tx_in.operation_datetime.label("in_operation_datetime"),
            tx_in.posting_datetime.label("in_posting_datetime"),
            tx_in.created_at.label("in_created_at"),
            tx_out.description_raw.label("out_description_raw"),
            tx_out.bank_category.label("out_bank_category"),
            tx_in.description_raw.label("in_description_raw"),
            tx_in.bank_category.label("in_bank_category"),
        )
        .select_from(TransferLink)
        .join(tx_out, tx_out.id == TransferLink.transaction_out_id)
        .join(tx_in, tx_in.id == TransferLink.transaction_in_id)
        .outerjoin(out_accounts, out_accounts.c.tx_id == tx_out.id)
        .outerjoin(in_accounts, in_accounts.c.tx_id == tx_in.id)
        .where(TransferLink.status == TransferStatus.confirmed.value)
    ).all()

    lane_counts: dict[tuple[str, str], int] = {}
    lane_delay_seconds: dict[tuple[str, str], list[float]] = {}
    lane_out_texts: dict[tuple[str, str], list[str]] = {}
    lane_in_texts: dict[tuple[str, str], list[str]] = {}

    for row in rows:
        out_account_id = row.out_account_id
        in_account_id = row.in_account_id
        if not out_account_id or not in_account_id:
            continue
        lane_key = (out_account_id, in_account_id)
        lane_counts[lane_key] = lane_counts.get(lane_key, 0) + 1

        out_ts = _select_timestamp(
            row.out_operation_datetime,
            row.out_posting_datetime,
            row.out_created_at,
        )
        in_ts = _select_timestamp(
            row.in_operation_datetime,
            row.in_posting_datetime,
            row.in_created_at,
        )
        if out_ts is not None and in_ts is not None:
            lane_delay_seconds.setdefault(lane_key, []).append(
                abs((in_ts - out_ts).total_seconds())
            )

        out_text = " ".join([row.out_description_raw or "", row.out_bank_category or ""]).strip()
        in_text = " ".join([row.in_description_raw or "", row.in_bank_category or ""]).strip()
        if out_text:
            lane_out_texts.setdefault(lane_key, []).append(out_text)
        if in_text:
            lane_in_texts.setdefault(lane_key, []).append(in_text)

    priors: dict[tuple[str, str], TransferLanePrior] = {}
    for lane_key, confirmations_count in lane_counts.items():
        delays = lane_delay_seconds.get(lane_key, [])
        typical_delay_window_seconds = max(
            30 * 60.0,
            _percentile_seconds(delays, percentile=0.90) if delays else 24 * 60 * 60.0,
        )
        priors[lane_key] = TransferLanePrior(
            account_out_id=lane_key[0],
            account_in_id=lane_key[1],
            confirmations_count=confirmations_count,
            typical_delay_window_seconds=typical_delay_window_seconds,
            out_description_patterns=_extract_lane_patterns(lane_out_texts.get(lane_key, [])),
            in_description_patterns=_extract_lane_patterns(lane_in_texts.get(lane_key, [])),
        )
    return priors


def detect_transfer_links_in_session(
    db: Session,
    *,
    window_days: int = 2,
    suggested_threshold: float = 0.80,
    auto_threshold: float = 0.92,
) -> TransferDetectionResult:
    if window_days <= 0:
        raise ValueError("window_days must be > 0")
    window = timedelta(days=window_days)

    tx_accounts = (
        select(
            transaction_statement_link.c.transaction_id.label("tx_id"),
            func.min(Statement.account_id).label("account_id"),
        )
        .select_from(transaction_statement_link)
        .join(StatementRow, StatementRow.id == transaction_statement_link.c.statement_row_id)
        .join(Statement, Statement.id == StatementRow.statement_id)
        .group_by(transaction_statement_link.c.transaction_id)
        .subquery()
    )
    lane_priors = _build_lane_priors(db, tx_accounts_subquery=tx_accounts)

    linked_ids: set[str] = set()
    for out_id, in_id in db.execute(
        select(TransferLink.transaction_out_id, TransferLink.transaction_in_id)
    ).all():
        if out_id:
            linked_ids.add(out_id)
        if in_id:
            linked_ids.add(in_id)

    rows = db.execute(
        select(
            Transaction.id,
            Transaction.direction,
            Transaction.currency,
            Transaction.amount,
            Transaction.operation_datetime,
            Transaction.posting_datetime,
            Transaction.created_at,
            Transaction.description_raw,
            Transaction.bank_category,
            Transaction.bank_reference_id,
            Transaction.account_id.label("transaction_account_id"),
            tx_accounts.c.account_id.label("statement_account_id"),
        ).outerjoin(tx_accounts, tx_accounts.c.tx_id == Transaction.id)
    ).all()

    outflows: list[TransferTx] = []
    inflows: list[TransferTx] = []

    for row in rows:
        tx_id = row.id
        if not tx_id or tx_id in linked_ids:
            continue

        account_id = row.transaction_account_id or row.statement_account_id
        if not account_id:
            continue

        timestamp = _tx_timestamp(row)
        if timestamp is None:
            continue

        amount_cents = _amount_to_cents(row.amount)
        if amount_cents is None:
            continue

        tx = TransferTx(
            id=tx_id,
            account_id=account_id,
            direction=row.direction,
            currency=row.currency or "RUB",
            amount_cents=amount_cents,
            timestamp=timestamp,
            description_raw=row.description_raw or "",
            bank_category=row.bank_category or "",
            bank_reference_id=row.bank_reference_id or "",
        )

        if tx.direction == "out":
            outflows.append(tx)
        elif tx.direction == "in":
            inflows.append(tx)

    scored_pairs = []
    for tx_out in outflows:
        for tx_in in inflows:
            lane_prior = lane_priors.get((tx_out.account_id, tx_in.account_id))
            scored = score_transfer_pair(tx_out, tx_in, window=window, lane_prior=lane_prior)
            if scored is not None:
                scored_pairs.append(scored)

    selected = select_transfer_links(
        scored_pairs,
        suggested_threshold=suggested_threshold,
        auto_threshold=auto_threshold,
    )

    links_created = 0
    transactions_marked_internal = 0

    for link in [*selected.auto_links, *selected.suggested_links]:
        db.add(
            TransferLink(
                transaction_out_id=link.transaction_out_id,
                transaction_in_id=link.transaction_in_id,
                status=link.status,
                match_score=link.score,
                rationale=link.rationale,
                fee_amount=link.fee_amount,
            )
        )
        links_created += 1

    if links_created:
        db.flush()

    for link in selected.auto_links:
        for tx_id in (link.transaction_out_id, link.transaction_in_id):
            tx = db.query(Transaction).filter(Transaction.id == tx_id).first()
            if not tx or tx.meaning == TransactionMeaning.internal_transfer.value:
                continue
            tx.meaning = TransactionMeaning.internal_transfer.value
            tx.meaning_confidence = link.score
            transactions_marked_internal += 1

    return TransferDetectionResult(
        links_created=links_created,
        auto_links_created=len(selected.auto_links),
        suggested_links_created=len(selected.suggested_links),
        transactions_marked_internal=transactions_marked_internal,
    )


def rebuild_all_transfer_links_in_session(
    db: Session,
    *,
    window_days: int = 2,
    suggested_threshold: float = 0.80,
    auto_threshold: float = 0.92,
) -> TransferDetectionResult:
    linked_ids = set(
        tx_id
        for out_id, in_id in db.execute(
            select(TransferLink.transaction_out_id, TransferLink.transaction_in_id)
        ).all()
        for tx_id in (out_id, in_id)
        if tx_id
    )

    if linked_ids:
        db.query(TransferLink).delete(synchronize_session=False)
        for tx in db.query(Transaction).filter(Transaction.id.in_(linked_ids)).all():
            if tx.meaning == TransactionMeaning.internal_transfer.value:
                tx.meaning = TransactionMeaning.unknown.value
                tx.meaning_confidence = None
        db.flush()

    return detect_transfer_links_in_session(
        db,
        window_days=window_days,
        suggested_threshold=suggested_threshold,
        auto_threshold=auto_threshold,
    )


def confirm_transfer_link(db: Session, *, link: TransferLink) -> int:
    link.status = TransferStatus.confirmed.value
    updated = 0
    for tx_id in (link.transaction_out_id, link.transaction_in_id):
        tx = db.query(Transaction).filter(Transaction.id == tx_id).first()
        if not tx:
            continue
        if tx.meaning == TransactionMeaning.internal_transfer.value:
            continue
        tx.meaning = TransactionMeaning.internal_transfer.value
        tx.meaning_confidence = float(link.match_score) if link.match_score is not None else 1.0
        updated += 1
    return updated


def reject_transfer_link(db: Session, *, link: TransferLink) -> int:
    link.status = TransferStatus.rejected.value
    updated = 0
    for tx_id in (link.transaction_out_id, link.transaction_in_id):
        tx = db.query(Transaction).filter(Transaction.id == tx_id).first()
        if not tx:
            continue
        if tx.meaning != TransactionMeaning.internal_transfer.value:
            continue
        tx.meaning = TransactionMeaning.unknown.value
        tx.meaning_confidence = None
        updated += 1
    return updated
