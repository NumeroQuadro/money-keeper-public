from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..models import (
    Account,
    BalanceSnapshot,
    ExceptionItem,
    ImportBatch,
    ImportFile,
    Rule,
    Statement,
    StatementRow,
    Transaction,
    TransferLink,
    transaction_statement_link,
)


@dataclass(frozen=True)
class DeleteImportFileResult:
    file_id: str
    batch_id: Optional[str]
    file_path: str
    deleted_statements: int
    deleted_statement_rows: int
    deleted_transactions: int
    deleted_transfer_links: int
    deleted_balance_snapshots: int
    deleted_exceptions: int
    deleted_import_batch: bool


@dataclass(frozen=True)
class DeleteStatementResult:
    statement_id: str
    pdf_path: str
    deleted_statement_rows: int
    deleted_transactions: int
    deleted_balance_snapshots: int
    deleted_exceptions: int
    deleted_transfer_links: int


@dataclass(frozen=True)
class DeleteImportBatchResult:
    batch_id: str
    deleted_import_files: int
    deleted_statements: int
    deleted_statement_rows: int
    deleted_transactions: int
    deleted_balance_snapshots: int
    deleted_exceptions: int
    deleted_transfer_links: int
    file_paths: list[str]


@dataclass(frozen=True)
class PurgeDataResult:
    deleted_import_batches: int
    deleted_import_files: int
    deleted_statements: int
    deleted_statement_rows: int
    deleted_transactions: int
    deleted_transfer_links: int
    deleted_balance_snapshots: int
    deleted_exceptions: int
    deleted_rules: int
    deleted_accounts: int
    file_paths: list[str]


def _chunked(seq: Sequence[str], *, size: int = 500) -> Iterable[list[str]]:
    if size <= 0:
        raise ValueError("size must be > 0")
    buf: list[str] = []
    for item in seq:
        buf.append(item)
        if len(buf) >= size:
            yield buf
            buf = []
    if buf:
        yield buf


def delete_import_file_data(db: Session, *, file_id: str) -> DeleteImportFileResult:
    import_file = db.query(ImportFile).filter(ImportFile.id == file_id).first()
    if not import_file:
        raise ValueError("Import file not found")

    batch_id = import_file.batch_id
    file_path = import_file.file_path or ""

    statements = db.query(Statement.id).filter(Statement.pdf_path == file_path).all()
    statement_ids = [row[0] for row in statements]

    deleted_statement_rows = 0
    deleted_transactions = 0
    deleted_transfer_links = 0
    deleted_balance_snapshots = 0
    deleted_exceptions = 0

    affected_transaction_ids: list[str] = []
    if statement_ids:
        statement_row_ids = [
            row[0]
            for row in db.query(StatementRow.id)
            .filter(StatementRow.statement_id.in_(statement_ids))
            .all()
        ]

        if statement_row_ids:
            affected_transaction_ids = [
                row[0]
                for row in db.execute(
                    select(transaction_statement_link.c.transaction_id)
                    .where(transaction_statement_link.c.statement_row_id.in_(statement_row_ids))
                    .distinct()
                ).all()
            ]

            db.execute(
                transaction_statement_link.delete().where(
                    transaction_statement_link.c.statement_row_id.in_(statement_row_ids)
                )
            )

            for chunk in _chunked(statement_row_ids):
                deleted_statement_rows += (
                    db.query(StatementRow)
                    .filter(StatementRow.id.in_(chunk))
                    .delete(synchronize_session=False)
                )

        for chunk in _chunked(statement_ids):
            deleted_balance_snapshots += (
                db.query(BalanceSnapshot)
                .filter(BalanceSnapshot.statement_id.in_(chunk))
                .delete(synchronize_session=False)
            )
            deleted_exceptions += (
                db.query(ExceptionItem)
                .filter(
                    ExceptionItem.entity_type == "statement",
                    ExceptionItem.entity_id.in_(chunk),
                )
                .delete(synchronize_session=False)
            )

        for chunk in _chunked(statement_ids):
            db.query(Statement).filter(Statement.id.in_(chunk)).delete(synchronize_session=False)

    if affected_transaction_ids:
        remaining = set(
            row[0]
            for row in db.execute(
                select(transaction_statement_link.c.transaction_id)
                .where(transaction_statement_link.c.transaction_id.in_(affected_transaction_ids))
                .distinct()
            ).all()
        )
        orphan_transaction_ids = [
            tx_id for tx_id in affected_transaction_ids if tx_id not in remaining
        ]

        if orphan_transaction_ids:
            for chunk in _chunked(orphan_transaction_ids):
                deleted_transfer_links += (
                    db.query(TransferLink)
                    .filter(
                        or_(
                            TransferLink.transaction_out_id.in_(chunk),
                            TransferLink.transaction_in_id.in_(chunk),
                        )
                    )
                    .delete(synchronize_session=False)
                )

                deleted_exceptions += (
                    db.query(ExceptionItem)
                    .filter(
                        ExceptionItem.entity_type == "transaction",
                        ExceptionItem.entity_id.in_(chunk),
                    )
                    .delete(synchronize_session=False)
                )

                deleted_transactions += (
                    db.query(Transaction)
                    .filter(Transaction.id.in_(chunk))
                    .delete(synchronize_session=False)
                )

    deleted_exceptions += (
        db.query(ExceptionItem)
        .filter(
            ExceptionItem.entity_type == "import_file",
            ExceptionItem.entity_id == import_file.id,
        )
        .delete(synchronize_session=False)
    )

    db.delete(import_file)
    db.flush()

    deleted_import_batch = False
    if batch_id:
        remaining_files = (
            db.query(func.count(ImportFile.id))
            .filter(
                ImportFile.batch_id == batch_id,
            )
            .scalar()
            or 0
        )
        if remaining_files == 0:
            db.query(ImportBatch).filter(ImportBatch.id == batch_id).delete(
                synchronize_session=False
            )
            deleted_import_batch = True

    return DeleteImportFileResult(
        file_id=import_file.id,
        batch_id=batch_id,
        file_path=file_path,
        deleted_statements=len(statement_ids),
        deleted_statement_rows=deleted_statement_rows,
        deleted_transactions=deleted_transactions,
        deleted_transfer_links=deleted_transfer_links,
        deleted_balance_snapshots=deleted_balance_snapshots,
        deleted_exceptions=deleted_exceptions,
        deleted_import_batch=deleted_import_batch,
    )


def delete_statement_data(db: Session, *, statement_id: str) -> DeleteStatementResult:
    statement = db.query(Statement).filter(Statement.id == statement_id).first()
    if not statement:
        raise ValueError("Statement not found")

    pdf_path = statement.pdf_path or ""

    statement_row_ids = [
        row[0]
        for row in db.query(StatementRow.id).filter(StatementRow.statement_id == statement_id).all()
    ]

    deleted_statement_rows = 0
    deleted_transactions = 0
    deleted_balance_snapshots = 0
    deleted_exceptions = 0
    deleted_transfer_links = 0

    affected_transaction_ids: list[str] = []
    if statement_row_ids:
        affected_transaction_ids = [
            row[0]
            for row in db.execute(
                select(transaction_statement_link.c.transaction_id)
                .where(transaction_statement_link.c.statement_row_id.in_(statement_row_ids))
                .distinct()
            ).all()
        ]

        db.execute(
            transaction_statement_link.delete().where(
                transaction_statement_link.c.statement_row_id.in_(statement_row_ids)
            )
        )

        for chunk in _chunked(statement_row_ids):
            deleted_statement_rows += (
                db.query(StatementRow)
                .filter(StatementRow.id.in_(chunk))
                .delete(synchronize_session=False)
            )

    deleted_balance_snapshots += (
        db.query(BalanceSnapshot)
        .filter(BalanceSnapshot.statement_id == statement_id)
        .delete(synchronize_session=False)
    )

    deleted_exceptions += (
        db.query(ExceptionItem)
        .filter(
            ExceptionItem.entity_type == "statement",
            ExceptionItem.entity_id == statement_id,
        )
        .delete(synchronize_session=False)
    )

    db.query(Statement).filter(Statement.id == statement_id).delete(synchronize_session=False)

    if affected_transaction_ids:
        remaining = set(
            row[0]
            for row in db.execute(
                select(transaction_statement_link.c.transaction_id)
                .where(transaction_statement_link.c.transaction_id.in_(affected_transaction_ids))
                .distinct()
            ).all()
        )
        orphan_transaction_ids = [
            tx_id for tx_id in affected_transaction_ids if tx_id not in remaining
        ]

        if orphan_transaction_ids:
            for chunk in _chunked(orphan_transaction_ids):
                deleted_transfer_links += (
                    db.query(TransferLink)
                    .filter(
                        or_(
                            TransferLink.transaction_out_id.in_(chunk),
                            TransferLink.transaction_in_id.in_(chunk),
                        )
                    )
                    .delete(synchronize_session=False)
                )

                deleted_exceptions += (
                    db.query(ExceptionItem)
                    .filter(
                        ExceptionItem.entity_type == "transaction",
                        ExceptionItem.entity_id.in_(chunk),
                    )
                    .delete(synchronize_session=False)
                )

                deleted_transactions += (
                    db.query(Transaction)
                    .filter(Transaction.id.in_(chunk))
                    .delete(synchronize_session=False)
                )

    return DeleteStatementResult(
        statement_id=statement_id,
        pdf_path=pdf_path,
        deleted_statement_rows=deleted_statement_rows,
        deleted_transactions=deleted_transactions,
        deleted_balance_snapshots=deleted_balance_snapshots,
        deleted_exceptions=deleted_exceptions,
        deleted_transfer_links=deleted_transfer_links,
    )


def delete_import_batch_data(db: Session, *, batch_id: str) -> DeleteImportBatchResult:
    batch = db.query(ImportBatch).filter(ImportBatch.id == batch_id).first()
    if not batch:
        raise ValueError("Import batch not found")

    deleted_import_files = 0
    deleted_statements = 0
    deleted_statement_rows = 0
    deleted_transactions = 0
    deleted_balance_snapshots = 0
    deleted_exceptions = 0
    deleted_transfer_links = 0
    file_paths: list[str] = []

    file_ids = [
        item.id for item in db.query(ImportFile).filter(ImportFile.batch_id == batch_id).all()
    ]
    for file_id in file_ids:
        result = delete_import_file_data(db, file_id=file_id)
        deleted_import_files += 1
        deleted_statements += result.deleted_statements
        deleted_statement_rows += result.deleted_statement_rows
        deleted_transactions += result.deleted_transactions
        deleted_transfer_links += result.deleted_transfer_links
        deleted_balance_snapshots += result.deleted_balance_snapshots
        deleted_exceptions += result.deleted_exceptions
        file_paths.append(result.file_path)

    # Ensure batch is removed even if no files were present.
    db.query(ImportBatch).filter(ImportBatch.id == batch_id).delete(synchronize_session=False)

    return DeleteImportBatchResult(
        batch_id=batch_id,
        deleted_import_files=deleted_import_files,
        deleted_statements=deleted_statements,
        deleted_statement_rows=deleted_statement_rows,
        deleted_transactions=deleted_transactions,
        deleted_balance_snapshots=deleted_balance_snapshots,
        deleted_exceptions=deleted_exceptions,
        deleted_transfer_links=deleted_transfer_links,
        file_paths=file_paths,
    )


def purge_all_data(db: Session) -> PurgeDataResult:
    file_paths = [
        row[0] for row in db.query(ImportFile.file_path).filter(ImportFile.file_path != "").all()
    ]

    deleted_transfer_links = db.query(TransferLink).delete(synchronize_session=False)
    db.execute(transaction_statement_link.delete())
    deleted_statement_rows = db.query(StatementRow).delete(synchronize_session=False)
    deleted_balance_snapshots = db.query(BalanceSnapshot).delete(synchronize_session=False)
    deleted_statements = db.query(Statement).delete(synchronize_session=False)
    deleted_transactions = db.query(Transaction).delete(synchronize_session=False)
    deleted_exceptions = db.query(ExceptionItem).delete(synchronize_session=False)
    deleted_import_files = db.query(ImportFile).delete(synchronize_session=False)
    deleted_import_batches = db.query(ImportBatch).delete(synchronize_session=False)
    deleted_rules = db.query(Rule).delete(synchronize_session=False)
    deleted_accounts = db.query(Account).delete(synchronize_session=False)

    return PurgeDataResult(
        deleted_import_batches=deleted_import_batches,
        deleted_import_files=deleted_import_files,
        deleted_statements=deleted_statements,
        deleted_statement_rows=deleted_statement_rows,
        deleted_transactions=deleted_transactions,
        deleted_transfer_links=deleted_transfer_links,
        deleted_balance_snapshots=deleted_balance_snapshots,
        deleted_exceptions=deleted_exceptions,
        deleted_rules=deleted_rules,
        deleted_accounts=deleted_accounts,
        file_paths=file_paths,
    )
