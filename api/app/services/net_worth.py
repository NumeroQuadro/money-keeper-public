from __future__ import annotations

import hashlib
import logging
import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Iterable, Literal, Optional, overload

from sqlalchemy import case, or_, text
from sqlalchemy.orm import Session

from ..db import SessionLocal
from ..domain.transactions import TxCandidate, fingerprint
from ..domain.net_worth import (
    AccountSnapshotScope,
    BALANCE_SNAPSHOT_METHOD_CLOSING,
    BALANCE_SNAPSHOT_METHOD_OPENING,
    BalanceSnapshotCandidate,
    StatementBalanceInput,
    build_balance_snapshots,
    compute_net_worth_timeline as compute_net_worth_timeline_from_snapshots,
)
from ..models import (
    Account,
    BalanceSnapshot,
    ExceptionItem,
    Statement,
    StatementRow,
    Transaction,
    TransferLink,
    transaction_statement_link,
)

EXCEPTION_TYPE_RECONCILIATION_MISMATCH = "reconciliation_mismatch"
logger = logging.getLogger(__name__)

_SNAPSHOT_METHOD_PRIORITY = {
    BALANCE_SNAPSHOT_METHOD_OPENING: 0,
    BALANCE_SNAPSHOT_METHOD_CLOSING: 2,
}


@contextmanager
def _session_scope() -> Iterable[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _as_decimal(value: object) -> Optional[Decimal]:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float, str)):
        return Decimal(str(value))
    return None


def statement_type_to_account_type(statement_type: str) -> str:
    normalized = (statement_type or "").strip().lower()
    if normalized in {"card", "payment", "savings", "deposit", "wallet"}:
        return normalized
    return "unknown"


def _mask_identifier(value: str) -> str:
    digits = re.sub(r"[^0-9]", "", value or "")
    if not digits:
        return ""
    if len(digits) <= 4:
        return f"****{digits}"
    return f"****{digits[-4:]}"


def _account_lock_key(*, provider: str, account_type: str, display_name: str, currency: str) -> int:
    payload = "|".join((provider, account_type, display_name, currency))
    raw = hashlib.sha1(payload.encode("utf-8")).digest()[:8]
    value = int.from_bytes(raw, "big", signed=False)
    if value >= 2**63:
        value -= 2**64
    return value


def _lock_account_identity(
    db: Session,
    *,
    provider: str,
    account_type: str,
    display_name: str,
    currency: str,
) -> None:
    bind = db.get_bind()
    if bind is None or bind.dialect.name != "postgresql":
        return

    db.execute(
        text("select pg_advisory_xact_lock(:key)"),
        {
            "key": _account_lock_key(
                provider=provider,
                account_type=account_type,
                display_name=display_name,
                currency=currency,
            )
        },
    )


def _load_statement_transactions(db: Session, *, statement_id: str) -> list[Transaction]:
    transaction_ids = (
        db.query(Transaction.id)
        .outerjoin(
            transaction_statement_link,
            transaction_statement_link.c.transaction_id == Transaction.id,
        )
        .outerjoin(
            StatementRow,
            StatementRow.id == transaction_statement_link.c.statement_row_id,
        )
        .filter(
            or_(
                Transaction.source_statement_id == statement_id,
                StatementRow.statement_id == statement_id,
            )
        )
        .distinct()
        .subquery()
    )
    return (
        db.query(Transaction)
        .join(transaction_ids, transaction_ids.c.id == Transaction.id)
        .order_by(Transaction.created_at.asc(), Transaction.id.asc())
        .all()
    )


def _transaction_match_query(
    db: Session,
    *,
    transaction: Transaction,
    target_account_id: str,
):
    query = db.query(Transaction).filter(
        Transaction.account_id == target_account_id,
        Transaction.id != transaction.id,
        Transaction.amount == transaction.amount,
        Transaction.currency == transaction.currency,
        Transaction.direction == transaction.direction,
        Transaction.description_raw == transaction.description_raw,
        Transaction.bank_reference_id == transaction.bank_reference_id,
        Transaction.bank_category == transaction.bank_category,
    )
    if transaction.operation_datetime is None:
        query = query.filter(Transaction.operation_datetime.is_(None))
    else:
        query = query.filter(Transaction.operation_datetime == transaction.operation_datetime)
    if transaction.posting_datetime is None:
        query = query.filter(Transaction.posting_datetime.is_(None))
    else:
        query = query.filter(Transaction.posting_datetime == transaction.posting_datetime)
    return query


def _find_matching_transaction(
    db: Session,
    *,
    transaction: Transaction,
    target_account_id: str,
) -> Optional[Transaction]:
    candidates = (
        _transaction_match_query(db, transaction=transaction, target_account_id=target_account_id)
        .order_by(Transaction.created_at.asc(), Transaction.id.asc())
        .all()
    )
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    source_raw_texts = {row.raw_text or "" for row in transaction.statement_rows if row.raw_text}
    if source_raw_texts:
        narrowed = [
            candidate
            for candidate in candidates
            if source_raw_texts
            & {row.raw_text or "" for row in candidate.statement_rows if row.raw_text}
        ]
        if len(narrowed) == 1:
            return narrowed[0]
        if narrowed:
            candidates = narrowed

    return candidates[0]


def _build_dedup_key_for_transaction(
    transaction: Transaction,
    *,
    target_account_id: str,
) -> str:
    linked_row = transaction.statement_rows[0] if transaction.statement_rows else None
    raw_text = (
        linked_row.raw_text
        if linked_row is not None and linked_row.raw_text
        else transaction.description_raw or ""
    )
    candidate = TxCandidate(
        account_id=target_account_id,
        statement_row_id=linked_row.id if linked_row is not None else "",
        operation_datetime=transaction.operation_datetime,
        posting_datetime=transaction.posting_datetime,
        amount=_as_decimal(transaction.amount) or Decimal("0"),
        currency=transaction.currency or "RUB",
        direction=transaction.direction or "out",
        description_raw=transaction.description_raw or "",
        merchant_normalized=transaction.merchant_normalized or "",
        bank_reference_id=transaction.bank_reference_id or "",
        bank_category=transaction.bank_category or "",
        meaning=transaction.meaning or "unknown",
        meaning_confidence=_as_decimal(transaction.meaning_confidence),
        category=transaction.category or "",
        tags=transaction.tags,
        review_status=transaction.review_status or "needs_review",
        raw_text=raw_text,
        timestamp_precision=transaction.timestamp_precision or "unknown",
        source_statement_id=transaction.source_statement_id or "",
        source_page_number=transaction.source_page_number or 0,
        source_row_index=transaction.source_row_index or 0,
    )
    return fingerprint(candidate)


def _merge_transfer_link_metadata(existing: TransferLink, incoming: TransferLink) -> None:
    status_rank = {
        "confirmed": 4,
        "rejected": 3,
        "auto": 2,
        "suggested": 1,
    }
    if status_rank.get(incoming.status, 0) > status_rank.get(existing.status, 0):
        existing.status = incoming.status
        existing.rationale = incoming.rationale
    if incoming.match_score is not None and (
        existing.match_score is None or incoming.match_score > existing.match_score
    ):
        existing.match_score = incoming.match_score
    if existing.fee_amount is None and incoming.fee_amount is not None:
        existing.fee_amount = incoming.fee_amount


def _remap_transfer_links(
    db: Session,
    *,
    source_transaction_id: str,
    target_transaction_id: str,
) -> None:
    links = (
        db.query(TransferLink)
        .filter(
            or_(
                TransferLink.transaction_out_id == source_transaction_id,
                TransferLink.transaction_in_id == source_transaction_id,
            )
        )
        .all()
    )
    for link in links:
        new_out_id = (
            target_transaction_id
            if link.transaction_out_id == source_transaction_id
            else link.transaction_out_id
        )
        new_in_id = (
            target_transaction_id
            if link.transaction_in_id == source_transaction_id
            else link.transaction_in_id
        )
        if new_out_id == new_in_id:
            db.delete(link)
            continue

        existing = (
            db.query(TransferLink)
            .filter(TransferLink.transaction_out_id == new_out_id)
            .filter(TransferLink.transaction_in_id == new_in_id)
            .filter(TransferLink.id != link.id)
            .first()
        )
        if existing is not None:
            _merge_transfer_link_metadata(existing, link)
            db.delete(link)
            continue

        link.transaction_out_id = new_out_id
        link.transaction_in_id = new_in_id


def _merge_transactions(
    db: Session,
    *,
    source: Transaction,
    target: Transaction,
) -> None:
    for row in source.statement_rows:
        if row not in target.statement_rows:
            target.statement_rows.append(row)
    _remap_transfer_links(
        db,
        source_transaction_id=source.id,
        target_transaction_id=target.id,
    )
    db.flush()
    db.delete(source)


def _reconcile_statement_transactions_to_account(
    db: Session,
    *,
    statement: Statement,
    target_account_id: Optional[str],
) -> set[str]:
    if not target_account_id:
        return set()

    moved_account_ids: set[str] = set()
    for transaction in _load_statement_transactions(db, statement_id=statement.id):
        if transaction.account_id == target_account_id:
            continue

        if transaction.account_id:
            moved_account_ids.add(transaction.account_id)
        existing = _find_matching_transaction(
            db,
            transaction=transaction,
            target_account_id=target_account_id,
        )
        if existing is not None:
            _merge_transactions(db, source=transaction, target=existing)
            continue

        transaction.account_id = target_account_id
        transaction.dedup_key = _build_dedup_key_for_transaction(
            transaction,
            target_account_id=target_account_id,
        )

    return moved_account_ids


def _reconcile_statement_balance_snapshots_to_account(
    db: Session,
    *,
    statement: Statement,
    target_account_id: Optional[str],
) -> set[str]:
    if not target_account_id:
        return set()

    moved_account_ids: set[str] = set()
    snapshots = (
        db.query(BalanceSnapshot)
        .filter(BalanceSnapshot.statement_id == statement.id)
        .order_by(BalanceSnapshot.timestamp.asc(), BalanceSnapshot.id.asc())
        .all()
    )
    for snapshot in snapshots:
        if snapshot.account_id == target_account_id:
            continue

        if snapshot.account_id:
            moved_account_ids.add(snapshot.account_id)
        existing = (
            db.query(BalanceSnapshot)
            .filter(BalanceSnapshot.id != snapshot.id)
            .filter(BalanceSnapshot.account_id == target_account_id)
            .filter(BalanceSnapshot.statement_id == snapshot.statement_id)
            .filter(BalanceSnapshot.method == snapshot.method)
            .filter(BalanceSnapshot.timestamp == snapshot.timestamp)
            .first()
        )
        if existing is not None:
            db.delete(snapshot)
            continue
        snapshot.account_id = target_account_id

    return moved_account_ids


def _delete_account_if_orphaned(db: Session, *, account_id: str) -> None:
    if not account_id:
        return
    db.flush()
    if db.query(Statement.id).filter(Statement.account_id == account_id).first() is not None:
        return
    if db.query(Transaction.id).filter(Transaction.account_id == account_id).first() is not None:
        return
    if (
        db.query(BalanceSnapshot.id).filter(BalanceSnapshot.account_id == account_id).first()
        is not None
    ):
        return

    account = db.query(Account).filter(Account.id == account_id).first()
    if account is not None:
        db.delete(account)


@overload
def get_or_create_account(
    db: Session,
    *,
    provider: str,
    statement_type: str,
    account_display: str,
    currency: str,
    return_created: Literal[False] = False,
) -> Account: ...


@overload
def get_or_create_account(
    db: Session,
    *,
    provider: str,
    statement_type: str,
    account_display: str,
    currency: str,
    return_created: Literal[True],
) -> tuple[Account, bool]: ...


def get_or_create_account(
    db: Session,
    *,
    provider: str,
    statement_type: str,
    account_display: str,
    currency: str,
    return_created: bool = False,
):
    account_type = statement_type_to_account_type(statement_type)
    display_name = account_display or ""
    currency = currency or "RUB"
    _lock_account_identity(
        db,
        provider=provider,
        account_type=account_type,
        display_name=display_name,
        currency=currency,
    )

    existing = (
        db.query(Account)
        .filter(
            Account.provider == provider,
            Account.account_type == account_type,
            Account.display_name == display_name,
            Account.currency == currency,
        )
        .order_by(Account.created_at.asc(), Account.id.asc())
        .first()
    )
    if existing:
        if return_created:
            return existing, False
        return existing

    item = Account(
        provider=provider,
        account_type=account_type,
        display_name=display_name,
        masked_identifier=_mask_identifier(display_name),
        currency=currency,
        include_in_net_worth=True,
    )
    db.add(item)
    db.flush()
    if return_created:
        return item, True
    return item


def _snapshot_method_order(column):
    return case(
        (
            column == BALANCE_SNAPSHOT_METHOD_OPENING,
            _SNAPSHOT_METHOD_PRIORITY[BALANCE_SNAPSHOT_METHOD_OPENING],
        ),
        (
            column == BALANCE_SNAPSHOT_METHOD_CLOSING,
            _SNAPSHOT_METHOD_PRIORITY[BALANCE_SNAPSHOT_METHOD_CLOSING],
        ),
        else_=1,
    )


def ensure_statement_balance_snapshots(
    db: Session, *, statement: Statement
) -> list[BalanceSnapshot]:
    if not statement.account_id:
        return []

    existing_methods = {
        row.method
        for row in db.query(BalanceSnapshot)
        .filter(BalanceSnapshot.statement_id == statement.id)
        .all()
    }
    created: list[BalanceSnapshot] = []
    candidates = build_balance_snapshots(
        [
            StatementBalanceInput(
                account_id=statement.account_id,
                statement_id=statement.id,
                currency=statement.currency or "RUB",
                period_start=statement.period_start,
                period_end=statement.period_end,
                generated_at=statement.generated_at,
                created_at=statement.created_at,
                opening_balance=_as_decimal(statement.opening_balance),
                closing_balance=_as_decimal(statement.closing_balance),
                parse_confidence=_as_decimal(statement.parse_confidence),
                reconcile_status=statement.reconcile_status or "unknown",
            )
        ]
    )
    for candidate in candidates:
        if candidate.method in existing_methods:
            continue
        created.append(
            BalanceSnapshot(
                account_id=candidate.account_id,
                timestamp=candidate.timestamp,
                balance=float(candidate.balance),
                method=candidate.method,
                confidence=float(candidate.confidence)
                if candidate.confidence is not None
                else None,
                statement_id=candidate.statement_id,
            )
        )

    for row in created:
        db.add(row)
    return created


def _build_reconciliation_payload_from_statement(statement: Statement) -> Optional[dict]:
    opening_balance = _as_decimal(statement.opening_balance)
    closing_balance = _as_decimal(statement.closing_balance)
    if opening_balance is None or closing_balance is None:
        return None

    total_credits = _as_decimal(statement.total_credits)
    total_debits = _as_decimal(statement.total_debits)
    if total_credits is None or total_debits is None:
        return None

    expected = opening_balance + total_credits - total_debits
    delta = expected - closing_balance

    return {
        "opening_balance": float(opening_balance),
        "closing_balance": float(closing_balance),
        "credits": float(total_credits),
        "debits": float(total_debits),
        "expected_closing_balance": float(expected),
        "delta": float(delta),
        "method": "statement_totals",
    }


def ensure_reconciliation_exception(
    db: Session,
    *,
    statement: Statement,
    payload: Optional[dict] = None,
) -> Optional[ExceptionItem]:
    if (statement.reconcile_status or "") != "mismatch":
        return None

    exists = (
        db.query(ExceptionItem.id)
        .filter(
            ExceptionItem.exception_type == EXCEPTION_TYPE_RECONCILIATION_MISMATCH,
            ExceptionItem.entity_type == "statement",
            ExceptionItem.entity_id == statement.id,
        )
        .first()
    )
    if exists:
        return None

    item = ExceptionItem(
        exception_type=EXCEPTION_TYPE_RECONCILIATION_MISMATCH,
        severity="high",
        status="open",
        entity_type="statement",
        entity_id=statement.id,
        rationale="Statement reconciliation mismatch (opening + credits - debits != closing)",
        payload=payload,
    )
    db.add(item)
    return item


@dataclass(frozen=True)
class NetWorthRebuildResult:
    statements_scanned: int
    statements_linked: int
    accounts_created: int
    snapshots_created: int
    exceptions_created: int


def rebuild_net_worth_artifacts_in_session(db: Session) -> NetWorthRebuildResult:
    statements_scanned = 0
    statements_linked = 0
    accounts_created = 0
    snapshots_created = 0
    exceptions_created = 0

    for statement in db.query(Statement).order_by(Statement.created_at.asc()).all():
        statements_scanned += 1

        account_before = statement.account_id

        account, created = get_or_create_account(
            db,
            provider=statement.provider,
            statement_type=statement.statement_type,
            account_display=statement.account_display,
            currency=statement.currency,
            return_created=True,
        )
        if statement.account_id != account.id:
            statement.account_id = account.id
        moved_transaction_accounts = _reconcile_statement_transactions_to_account(
            db,
            statement=statement,
            target_account_id=statement.account_id,
        )
        moved_snapshot_accounts = _reconcile_statement_balance_snapshots_to_account(
            db,
            statement=statement,
            target_account_id=statement.account_id,
        )

        if account_before is None and statement.account_id is not None:
            statements_linked += 1

        if created:
            accounts_created += 1

        snapshots_before = (
            db.query(BalanceSnapshot.id)
            .filter(BalanceSnapshot.statement_id == statement.id)
            .count()
        )
        ensure_statement_balance_snapshots(db, statement=statement)
        snapshots_after = (
            db.query(BalanceSnapshot.id)
            .filter(BalanceSnapshot.statement_id == statement.id)
            .count()
        )
        if snapshots_after > snapshots_before:
            snapshots_created += snapshots_after - snapshots_before

        exc_before = (
            db.query(ExceptionItem.id)
            .filter(
                ExceptionItem.exception_type == EXCEPTION_TYPE_RECONCILIATION_MISMATCH,
                ExceptionItem.entity_type == "statement",
                ExceptionItem.entity_id == statement.id,
            )
            .count()
        )

        ensure_reconciliation_exception(
            db, statement=statement, payload=_build_reconciliation_payload_from_statement(statement)
        )

        exc_after = (
            db.query(ExceptionItem.id)
            .filter(
                ExceptionItem.exception_type == EXCEPTION_TYPE_RECONCILIATION_MISMATCH,
                ExceptionItem.entity_type == "statement",
                ExceptionItem.entity_id == statement.id,
            )
            .count()
        )
        if exc_after > exc_before:
            exceptions_created += exc_after - exc_before

        for stale_account_id in sorted(
            (moved_transaction_accounts | moved_snapshot_accounts)
            - ({statement.account_id} if statement.account_id else set())
        ):
            _delete_account_if_orphaned(db, account_id=stale_account_id)

    return NetWorthRebuildResult(
        statements_scanned=statements_scanned,
        statements_linked=statements_linked,
        accounts_created=accounts_created,
        snapshots_created=snapshots_created,
        exceptions_created=exceptions_created,
    )


def rebuild_net_worth_artifacts() -> NetWorthRebuildResult:
    with _session_scope() as db:
        result = rebuild_net_worth_artifacts_in_session(db)
        db.commit()
        return result


def compute_net_worth_current(
    db: Session,
    *,
    currency: Optional[str] = None,
) -> dict:
    accounts_query = db.query(Account).filter(Account.include_in_net_worth.is_(True))
    if currency:
        accounts_query = accounts_query.filter(Account.currency == currency)
    accounts = accounts_query.order_by(Account.created_at.asc()).all()

    account_items = []
    totals: dict[str, Decimal] = {}

    for account in accounts:
        snapshot = (
            db.query(BalanceSnapshot)
            .filter(BalanceSnapshot.account_id == account.id)
            .order_by(
                BalanceSnapshot.timestamp.desc(),
                _snapshot_method_order(BalanceSnapshot.method).desc(),
                BalanceSnapshot.id.desc(),
            )
            .first()
        )

        balance_decimal = _as_decimal(snapshot.balance) if snapshot else None
        as_of = snapshot.timestamp if snapshot else None
        confidence = (
            float(_as_decimal(snapshot.confidence))
            if snapshot and snapshot.confidence is not None
            else None
        )
        statement_id = snapshot.statement_id if snapshot else None

        if balance_decimal is not None:
            totals[account.currency] = totals.get(account.currency, Decimal("0")) + balance_decimal

        account_items.append(
            {
                "account_id": account.id,
                "provider": account.provider,
                "account_type": account.account_type,
                "display_name": account.display_name,
                "masked_identifier": account.masked_identifier
                or _mask_identifier(account.display_name),
                "currency": account.currency,
                "balance": float(balance_decimal) if balance_decimal is not None else None,
                "as_of": as_of,
                "confidence": confidence,
                "statement_id": statement_id,
            }
        )

    totals_items = [
        {"currency": curr, "total_balance": float(total)}
        for curr, total in sorted(totals.items(), key=lambda kv: kv[0])
    ]

    return {
        "generated_at": datetime.now(tz=timezone.utc),
        "totals": totals_items,
        "accounts": account_items,
    }


def compute_net_worth_timeline(
    db: Session,
    *,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    currency: Optional[str] = None,
    granularity: str = "raw",
) -> dict:
    accounts_query = db.query(Account).filter(Account.include_in_net_worth.is_(True))
    if currency:
        accounts_query = accounts_query.filter(Account.currency == currency)
    accounts = accounts_query.order_by(Account.created_at.asc()).all()
    if not accounts:
        return {
            "generated_at": datetime.now(tz=timezone.utc),
            "granularity": granularity,
            "series": [],
        }

    scopes = [
        AccountSnapshotScope(
            account_id=account.id,
            currency=account.currency or "RUB",
            include_in_net_worth=bool(account.include_in_net_worth),
        )
        for account in accounts
    ]

    snapshots_query = db.query(BalanceSnapshot)
    if currency:
        account_ids = [scope.account_id for scope in scopes]
        if account_ids:
            snapshots_query = snapshots_query.filter(BalanceSnapshot.account_id.in_(account_ids))
    if end is not None:
        snapshots_query = snapshots_query.filter(BalanceSnapshot.timestamp <= end)
    snapshots = snapshots_query.order_by(
        BalanceSnapshot.timestamp.asc(),
        _snapshot_method_order(BalanceSnapshot.method).asc(),
        BalanceSnapshot.id.asc(),
    ).all()

    currency_by_account = {scope.account_id: scope.currency for scope in scopes}
    snapshot_candidates = [
        BalanceSnapshotCandidate(
            account_id=snapshot.account_id,
            statement_id=snapshot.statement_id,
            timestamp=snapshot.timestamp,
            balance=_as_decimal(snapshot.balance) or Decimal("0"),
            method=snapshot.method,
            confidence=_as_decimal(snapshot.confidence),
            currency=currency_by_account.get(snapshot.account_id, "RUB"),
        )
        for snapshot in snapshots
        if snapshot.account_id in currency_by_account
    ]

    return compute_net_worth_timeline_from_snapshots(
        snapshots=snapshot_candidates,
        accounts=scopes,
        start=start,
        end=end,
        granularity=granularity,
    )


def backfill_net_worth_artifacts_background() -> None:
    try:
        rebuild_net_worth_artifacts()
    except Exception:
        # Startup should not fail due to best-effort backfill.
        logger.warning(
            "Background net-worth backfill failed; continuing startup without blocking API.",
            exc_info=True,
        )
        return
