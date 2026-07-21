from __future__ import annotations

import unittest
from datetime import datetime

from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import (
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
from app.services.cleanup import (
    delete_import_batch_data,
    delete_import_file_data,
    delete_statement_data,
    purge_all_data,
)
from app.tests.db_test_utils import get_test_engine


class CleanupTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = get_test_engine()
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)
        self._Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    def test_delete_import_file_data_removes_statements_rows_snapshots_and_orphan_transactions(
        self,
    ) -> None:
        with self._Session() as db:
            account = Account(id="acc", provider="ozon", account_type="card", currency="RUB")
            db.add(account)
            db.flush()

            batch = ImportBatch(source="test", status="queued")
            db.add(batch)
            db.flush()

            import_file = ImportFile(
                batch_id=batch.id,
                file_name="bad.pdf",
                file_path="./data/uploads/bad.pdf",
                file_hash="abc",
                status="processed",
            )
            db.add(import_file)
            db.flush()

            statement = Statement(
                provider="ozon",
                account_id=None,
                account_display="x",
                statement_type="card",
                currency="RUB",
                period_start=datetime(2026, 1, 1),
                period_end=datetime(2026, 1, 31),
                opening_balance=100,
                closing_balance=200,
                total_credits=150,
                total_debits=50,
                reconcile_status="mismatch",
                pdf_path=import_file.file_path,
            )
            db.add(statement)
            db.flush()

            row = StatementRow(
                statement_id=statement.id,
                row_index=0,
                page_number=1,
                raw_text="row",
                raw_data={},
                amount=10,
                currency="RUB",
                direction="out",
                operation_date=datetime(2026, 1, 2),
            )
            db.add(row)
            db.flush()

            tx = Transaction(amount=10, currency="RUB", direction="out", description_raw="x")
            db.add(tx)
            db.flush()

            db.execute(
                transaction_statement_link.insert().values(
                    transaction_id=tx.id, statement_row_id=row.id
                )
            )

            other_statement = Statement(
                provider="ozon",
                account_id=None,
                account_display="y",
                statement_type="card",
                currency="RUB",
                pdf_path="./data/uploads/other.pdf",
            )
            db.add(other_statement)
            db.flush()
            other_row = StatementRow(
                statement_id=other_statement.id,
                row_index=0,
                page_number=1,
                raw_text="row2",
                raw_data={},
                amount=10,
                currency="RUB",
                direction="out",
            )
            db.add(other_row)
            db.flush()
            db.execute(
                transaction_statement_link.insert().values(
                    transaction_id=tx.id, statement_row_id=other_row.id
                )
            )

            db.add(
                BalanceSnapshot(
                    account_id="acc",
                    timestamp=datetime(2026, 1, 1),
                    balance=100,
                    method="statement_opening_balance",
                    confidence=0.5,
                    statement_id=statement.id,
                )
            )
            db.add(
                ExceptionItem(
                    exception_type="reconciliation_mismatch",
                    severity="high",
                    status="open",
                    entity_type="statement",
                    entity_id=statement.id,
                    rationale="mismatch",
                )
            )
            db.add(
                ExceptionItem(
                    exception_type="parsing_anomaly",
                    severity="high",
                    status="open",
                    entity_type="import_file",
                    entity_id=import_file.id,
                    rationale="bad file",
                )
            )
            db.commit()

            statement_id = statement.id
            row_id = row.id
            import_file_id = import_file.id

            result = delete_import_file_data(db, file_id=import_file.id)
            db.commit()

            self.assertEqual(result.deleted_statements, 1)
            self.assertEqual(db.query(ImportFile).count(), 0)
            self.assertEqual(db.query(ImportBatch).count(), 0)
            self.assertEqual(db.query(Statement).filter(Statement.id == statement_id).count(), 0)
            self.assertEqual(db.query(StatementRow).filter(StatementRow.id == row_id).count(), 0)
            self.assertEqual(
                db.query(BalanceSnapshot)
                .filter(BalanceSnapshot.statement_id == statement_id)
                .count(),
                0,
            )
            self.assertEqual(
                db.query(ExceptionItem)
                .filter(
                    ExceptionItem.entity_type == "statement",
                    ExceptionItem.entity_id == statement_id,
                )
                .count(),
                0,
            )
            self.assertEqual(
                db.query(ExceptionItem)
                .filter(
                    ExceptionItem.entity_type == "import_file",
                    ExceptionItem.entity_id == import_file_id,
                )
                .count(),
                0,
            )

            # Transaction is preserved because it is still linked to another statement row.
            self.assertEqual(db.query(Transaction).filter(Transaction.id == tx.id).count(), 1)

    def test_purge_all_data_deletes_everything(self) -> None:
        with self._Session() as db:
            batch = ImportBatch(source="test", status="queued")
            db.add(batch)
            db.flush()

            db.add(
                ImportFile(
                    batch_id=batch.id,
                    file_name="x.pdf",
                    file_path="./data/uploads/x.pdf",
                    file_hash="abc",
                    status="queued",
                )
            )
            db.add(Rule(name="r", pattern="p", updated_at=datetime(2026, 1, 1)))
            db.commit()

            result = purge_all_data(db)
            db.commit()

            self.assertGreaterEqual(len(result.file_paths), 1)
            self.assertEqual(db.query(ImportBatch).count(), 0)
            self.assertEqual(db.query(ImportFile).count(), 0)
            self.assertEqual(db.query(Statement).count(), 0)
            self.assertEqual(db.query(StatementRow).count(), 0)
            self.assertEqual(db.query(Transaction).count(), 0)
            self.assertEqual(db.query(ExceptionItem).count(), 0)
            self.assertEqual(db.query(BalanceSnapshot).count(), 0)
            self.assertEqual(db.query(Rule).count(), 0)

    def test_delete_statement_data_removes_only_orphans(self) -> None:
        with self._Session() as db:
            account = Account(id="acc", provider="ozon", account_type="card", currency="RUB")
            db.add(account)
            db.flush()

            statement_target = Statement(
                provider="ozon",
                account_id=account.id,
                account_display="target",
                statement_type="card",
                currency="RUB",
                pdf_path="./data/uploads/target.pdf",
            )
            statement_keep = Statement(
                provider="ozon",
                account_id=account.id,
                account_display="keep",
                statement_type="card",
                currency="RUB",
                pdf_path="./data/uploads/keep.pdf",
            )
            db.add_all([statement_target, statement_keep])
            db.flush()

            target_row_shared = StatementRow(
                statement_id=statement_target.id,
                row_index=0,
                page_number=1,
                raw_text="shared row",
                raw_data={},
                amount=100,
                currency="RUB",
                direction="out",
                operation_date=datetime(2026, 1, 1, 10, 0, 0),
            )
            target_row_orphan = StatementRow(
                statement_id=statement_target.id,
                row_index=1,
                page_number=1,
                raw_text="orphan row",
                raw_data={},
                amount=50,
                currency="RUB",
                direction="out",
                operation_date=datetime(2026, 1, 1, 11, 0, 0),
            )
            keep_row_shared = StatementRow(
                statement_id=statement_keep.id,
                row_index=0,
                page_number=1,
                raw_text="keep shared row",
                raw_data={},
                amount=100,
                currency="RUB",
                direction="out",
                operation_date=datetime(2026, 1, 2, 10, 0, 0),
            )
            keep_row_counterparty = StatementRow(
                statement_id=statement_keep.id,
                row_index=1,
                page_number=1,
                raw_text="counterparty",
                raw_data={},
                amount=50,
                currency="RUB",
                direction="in",
                operation_date=datetime(2026, 1, 1, 11, 5, 0),
            )
            db.add_all(
                [target_row_shared, target_row_orphan, keep_row_shared, keep_row_counterparty]
            )
            db.flush()

            tx_shared = Transaction(
                account_id=account.id,
                dedup_key="shared",
                amount=100,
                currency="RUB",
                direction="out",
                description_raw="shared tx",
            )
            tx_orphan = Transaction(
                account_id=account.id,
                dedup_key="orphan",
                amount=50,
                currency="RUB",
                direction="out",
                description_raw="orphan tx",
            )
            tx_counterparty = Transaction(
                account_id=account.id,
                dedup_key="counterparty",
                amount=50,
                currency="RUB",
                direction="in",
                description_raw="counterparty tx",
            )
            db.add_all([tx_shared, tx_orphan, tx_counterparty])
            db.flush()

            db.execute(
                transaction_statement_link.insert().values(
                    transaction_id=tx_shared.id, statement_row_id=target_row_shared.id
                )
            )
            db.execute(
                transaction_statement_link.insert().values(
                    transaction_id=tx_shared.id, statement_row_id=keep_row_shared.id
                )
            )
            db.execute(
                transaction_statement_link.insert().values(
                    transaction_id=tx_orphan.id, statement_row_id=target_row_orphan.id
                )
            )
            db.execute(
                transaction_statement_link.insert().values(
                    transaction_id=tx_counterparty.id, statement_row_id=keep_row_counterparty.id
                )
            )

            db.add(
                TransferLink(
                    transaction_out_id=tx_orphan.id,
                    transaction_in_id=tx_counterparty.id,
                    status="suggested",
                    match_score=0.90,
                    rationale="test",
                )
            )
            db.add(
                BalanceSnapshot(
                    account_id=account.id,
                    timestamp=datetime(2026, 1, 1),
                    balance=1000,
                    method="statement_closing_balance",
                    confidence=1.0,
                    statement_id=statement_target.id,
                )
            )
            db.add(
                ExceptionItem(
                    exception_type="reconciliation_mismatch",
                    severity="high",
                    status="open",
                    entity_type="statement",
                    entity_id=statement_target.id,
                    rationale="target mismatch",
                )
            )
            db.add(
                ExceptionItem(
                    exception_type="uncertain_transfer_match",
                    severity="medium",
                    status="open",
                    entity_type="transaction",
                    entity_id=tx_orphan.id,
                    rationale="review orphan transfer",
                )
            )
            db.commit()

            statement_target_id = statement_target.id
            statement_keep_id = statement_keep.id
            tx_orphan_id = tx_orphan.id
            tx_shared_id = tx_shared.id
            tx_counterparty_id = tx_counterparty.id

            result = delete_statement_data(db, statement_id=statement_target.id)
            db.commit()

            self.assertEqual(result.deleted_statement_rows, 2)
            self.assertEqual(result.deleted_transactions, 1)
            self.assertEqual(result.deleted_transfer_links, 1)
            self.assertEqual(result.deleted_balance_snapshots, 1)
            self.assertEqual(result.deleted_exceptions, 2)

            self.assertEqual(
                db.query(Statement).filter(Statement.id == statement_target_id).count(), 0
            )
            self.assertEqual(
                db.query(Statement).filter(Statement.id == statement_keep_id).count(), 1
            )
            self.assertEqual(
                db.query(StatementRow)
                .filter(StatementRow.statement_id == statement_target_id)
                .count(),
                0,
            )
            self.assertEqual(
                db.query(Transaction).filter(Transaction.id == tx_orphan_id).count(), 0
            )
            self.assertEqual(
                db.query(Transaction).filter(Transaction.id == tx_shared_id).count(), 1
            )
            self.assertEqual(
                db.query(Transaction).filter(Transaction.id == tx_counterparty_id).count(),
                1,
            )
            self.assertEqual(db.query(TransferLink).count(), 0)

    def test_delete_import_batch_data_aggregates_and_preserves_shared_transactions(self) -> None:
        with self._Session() as db:
            account = Account(id="acc", provider="ozon", account_type="card", currency="RUB")
            db.add(account)
            db.flush()

            batch_delete = ImportBatch(source="test", status="queued")
            batch_keep = ImportBatch(source="test", status="queued")
            db.add_all([batch_delete, batch_keep])
            db.flush()

            file_a = ImportFile(
                batch_id=batch_delete.id,
                file_name="a.pdf",
                file_path="./data/uploads/a.pdf",
                file_hash="hash-a",
                status="processed",
            )
            file_b = ImportFile(
                batch_id=batch_delete.id,
                file_name="b.pdf",
                file_path="./data/uploads/b.pdf",
                file_hash="hash-b",
                status="processed",
            )
            file_keep = ImportFile(
                batch_id=batch_keep.id,
                file_name="keep.pdf",
                file_path="./data/uploads/keep.pdf",
                file_hash="hash-keep",
                status="processed",
            )
            db.add_all([file_a, file_b, file_keep])
            db.flush()

            statement_a = Statement(
                provider="ozon",
                account_id=account.id,
                account_display="a",
                statement_type="card",
                currency="RUB",
                pdf_path=file_a.file_path,
            )
            statement_b = Statement(
                provider="ozon",
                account_id=account.id,
                account_display="b",
                statement_type="card",
                currency="RUB",
                pdf_path=file_b.file_path,
            )
            statement_keep = Statement(
                provider="ozon",
                account_id=account.id,
                account_display="keep",
                statement_type="card",
                currency="RUB",
                pdf_path=file_keep.file_path,
            )
            db.add_all([statement_a, statement_b, statement_keep])
            db.flush()

            row_a = StatementRow(
                statement_id=statement_a.id,
                row_index=0,
                page_number=1,
                raw_text="row a",
                raw_data={},
                amount=100,
                currency="RUB",
                direction="out",
            )
            row_b = StatementRow(
                statement_id=statement_b.id,
                row_index=0,
                page_number=1,
                raw_text="row b",
                raw_data={},
                amount=50,
                currency="RUB",
                direction="out",
            )
            row_keep_shared = StatementRow(
                statement_id=statement_keep.id,
                row_index=0,
                page_number=1,
                raw_text="row keep shared",
                raw_data={},
                amount=100,
                currency="RUB",
                direction="out",
            )
            row_keep_counterparty = StatementRow(
                statement_id=statement_keep.id,
                row_index=1,
                page_number=1,
                raw_text="row keep counterparty",
                raw_data={},
                amount=50,
                currency="RUB",
                direction="in",
            )
            db.add_all([row_a, row_b, row_keep_shared, row_keep_counterparty])
            db.flush()

            tx_shared = Transaction(
                account_id=account.id,
                dedup_key="batch-shared",
                amount=100,
                currency="RUB",
                direction="out",
                description_raw="shared tx",
            )
            tx_orphan = Transaction(
                account_id=account.id,
                dedup_key="batch-orphan",
                amount=50,
                currency="RUB",
                direction="out",
                description_raw="orphan tx",
            )
            tx_counterparty = Transaction(
                account_id=account.id,
                dedup_key="batch-counterparty",
                amount=50,
                currency="RUB",
                direction="in",
                description_raw="counterparty tx",
            )
            db.add_all([tx_shared, tx_orphan, tx_counterparty])
            db.flush()

            db.execute(
                transaction_statement_link.insert().values(
                    transaction_id=tx_shared.id, statement_row_id=row_a.id
                )
            )
            db.execute(
                transaction_statement_link.insert().values(
                    transaction_id=tx_shared.id, statement_row_id=row_keep_shared.id
                )
            )
            db.execute(
                transaction_statement_link.insert().values(
                    transaction_id=tx_orphan.id, statement_row_id=row_b.id
                )
            )
            db.execute(
                transaction_statement_link.insert().values(
                    transaction_id=tx_counterparty.id, statement_row_id=row_keep_counterparty.id
                )
            )

            db.add(
                TransferLink(
                    transaction_out_id=tx_orphan.id,
                    transaction_in_id=tx_counterparty.id,
                    status="suggested",
                    match_score=0.90,
                    rationale="batch-test",
                )
            )
            db.add_all(
                [
                    BalanceSnapshot(
                        account_id=account.id,
                        timestamp=datetime(2026, 1, 1),
                        balance=1000,
                        method="statement_closing_balance",
                        confidence=1.0,
                        statement_id=statement_a.id,
                    ),
                    BalanceSnapshot(
                        account_id=account.id,
                        timestamp=datetime(2026, 1, 2),
                        balance=950,
                        method="statement_closing_balance",
                        confidence=1.0,
                        statement_id=statement_b.id,
                    ),
                ]
            )
            db.add_all(
                [
                    ExceptionItem(
                        exception_type="reconciliation_mismatch",
                        severity="high",
                        status="open",
                        entity_type="statement",
                        entity_id=statement_a.id,
                        rationale="statement a mismatch",
                    ),
                    ExceptionItem(
                        exception_type="reconciliation_mismatch",
                        severity="high",
                        status="open",
                        entity_type="statement",
                        entity_id=statement_b.id,
                        rationale="statement b mismatch",
                    ),
                    ExceptionItem(
                        exception_type="parsing_anomaly",
                        severity="high",
                        status="open",
                        entity_type="import_file",
                        entity_id=file_a.id,
                        rationale="file a issue",
                    ),
                    ExceptionItem(
                        exception_type="parsing_anomaly",
                        severity="high",
                        status="open",
                        entity_type="import_file",
                        entity_id=file_b.id,
                        rationale="file b issue",
                    ),
                    ExceptionItem(
                        exception_type="uncertain_transfer_match",
                        severity="medium",
                        status="open",
                        entity_type="transaction",
                        entity_id=tx_orphan.id,
                        rationale="orphan transfer review",
                    ),
                ]
            )
            db.commit()

            batch_delete_id = batch_delete.id
            batch_keep_id = batch_keep.id
            statement_a_id = statement_a.id
            statement_b_id = statement_b.id
            statement_keep_id = statement_keep.id
            tx_orphan_id = tx_orphan.id
            tx_shared_id = tx_shared.id
            tx_counterparty_id = tx_counterparty.id
            file_a_path = file_a.file_path
            file_b_path = file_b.file_path

            result = delete_import_batch_data(db, batch_id=batch_delete.id)
            db.commit()

            self.assertEqual(result.deleted_import_files, 2)
            self.assertEqual(result.deleted_statements, 2)
            self.assertEqual(result.deleted_statement_rows, 2)
            self.assertEqual(result.deleted_transactions, 1)
            self.assertEqual(result.deleted_transfer_links, 1)
            self.assertEqual(result.deleted_balance_snapshots, 2)
            self.assertEqual(result.deleted_exceptions, 5)
            self.assertCountEqual(result.file_paths, [file_a_path, file_b_path])

            self.assertEqual(
                db.query(ImportBatch).filter(ImportBatch.id == batch_delete_id).count(), 0
            )
            self.assertEqual(
                db.query(ImportBatch).filter(ImportBatch.id == batch_keep_id).count(), 1
            )
            self.assertEqual(
                db.query(ImportFile).filter(ImportFile.batch_id == batch_delete_id).count(),
                0,
            )
            self.assertEqual(
                db.query(ImportFile).filter(ImportFile.batch_id == batch_keep_id).count(),
                1,
            )
            self.assertEqual(
                db.query(Statement)
                .filter(Statement.id.in_([statement_a_id, statement_b_id]))
                .count(),
                0,
            )
            self.assertEqual(
                db.query(Statement).filter(Statement.id == statement_keep_id).count(), 1
            )
            self.assertEqual(
                db.query(Transaction).filter(Transaction.id == tx_orphan_id).count(), 0
            )
            self.assertEqual(
                db.query(Transaction).filter(Transaction.id == tx_shared_id).count(), 1
            )
            self.assertEqual(
                db.query(Transaction).filter(Transaction.id == tx_counterparty_id).count(),
                1,
            )
            self.assertEqual(db.query(TransferLink).count(), 0)


if __name__ == "__main__":
    unittest.main()
