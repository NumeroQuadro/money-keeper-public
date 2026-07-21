from __future__ import annotations

import unittest
from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import sessionmaker

from app.api.imports import delete_import_batch
from app.api.statements import delete_statement
from app.db import Base
from app.models import (
    ExceptionItem,
    ImportBatch,
    ImportFile,
    Statement,
    StatementRow,
    Transaction,
    TransferLink,
    transaction_statement_link,
)
from app.services.import_processing import persist_parsed_bundles
from app.services.transfers import (
    confirm_transfer_link,
    detect_transfer_links_in_session,
    reject_transfer_link,
)
from app.tests.db_test_utils import get_test_engine
from app.tests.fixture_data import build_parsed_bundle_map, load_crossbank_dataset


class ImportPipelineIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = get_test_engine()
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)
        self._Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

        self.dataset = load_crossbank_dataset()
        self.bundle_map = build_parsed_bundle_map(self.dataset)

    def _create_import_with_bundles(
        self, db, *, name: str, bundle_ids: list[str]
    ) -> tuple[ImportBatch, ImportFile]:
        batch = ImportBatch(source="test", status="queued")
        db.add(batch)
        db.flush()

        import_file = ImportFile(
            batch_id=batch.id,
            file_name=f"{name}.pdf",
            file_path=f"/tmp/{name}.pdf",
            file_hash=f"hash-{name}",
            status="queued",
        )
        db.add(import_file)
        db.flush()

        bundles = [self.bundle_map[bundle_id] for bundle_id in bundle_ids]
        persist_parsed_bundles(db, import_file=import_file, bundles=bundles)
        import_file.status = "processed"
        return batch, import_file

    def _add_fallback_transfer_out(self, db) -> tuple[StatementRow, Transaction]:
        yandex_statement = (
            db.query(Statement)
            .filter(Statement.account_display == "acc_yandex")
            .order_by(Statement.created_at.asc())
            .first()
        )
        assert yandex_statement is not None
        assert yandex_statement.account_id is not None

        extra_row = StatementRow(
            statement_id=yandex_statement.id,
            row_index=999,
            page_number=1,
            raw_text="10.01.2026 10:50 Transfer fallback -5000.00",
            raw_data={"fixture": "fallback"},
            amount=5000.00,
            currency="RUB",
            direction="out",
            operation_date=datetime(2026, 1, 10, 10, 50, 0),
        )
        db.add(extra_row)
        db.flush()

        extra_tx = Transaction(
            account_id=yandex_statement.account_id,
            dedup_key="manual-fallback-out-5000",
            amount=5000.00,
            currency="RUB",
            direction="out",
            operation_datetime=datetime(2026, 1, 10, 10, 50, 0),
            description_raw="Transfer fallback",
            bank_reference_id="FALLBACK-5000",
            bank_category="transfer",
            meaning="unknown",
            review_status="needs_review",
        )
        db.add(extra_tx)
        db.flush()

        db.execute(
            transaction_statement_link.insert().values(
                transaction_id=extra_tx.id, statement_row_id=extra_row.id
            )
        )
        return extra_row, extra_tx

    def test_pipeline_dedupes_overlap_rows_into_canonical_transactions(self) -> None:
        with self._Session() as db:
            self._create_import_with_bundles(
                db,
                name="full",
                bundle_ids=["st_ozon_main", "st_ozon_overlap", "st_sber_main", "st_yandex_main"],
            )
            result = detect_transfer_links_in_session(db)
            db.commit()

            self.assertEqual(db.query(StatementRow).count(), 10)
            self.assertEqual(db.query(Transaction).count(), 9)

            overlap_transactions = (
                db.query(Transaction).filter(Transaction.bank_reference_id == "PAY-400").all()
            )
            self.assertEqual(len(overlap_transactions), 1)
            self.assertEqual(len(overlap_transactions[0].statement_rows), 2)

            status_counts = {
                status: count
                for status, count in db.query(TransferLink.status, func.count(TransferLink.id))
                .group_by(TransferLink.status)
                .all()
            }
            self.assertEqual(result.links_created, 3)
            self.assertEqual(status_counts.get("auto"), 2)
            self.assertEqual(status_counts.get("suggested"), 1)

    def test_full_history_monthly_overlap_uses_fuzzy_dedupe(self) -> None:
        with self._Session() as db:
            self._create_import_with_bundles(
                db,
                name="history",
                bundle_ids=["st_ozon_full_history"],
            )
            db.commit()

            self._create_import_with_bundles(
                db,
                name="monthly",
                bundle_ids=["st_ozon_monthly_overlap"],
            )
            db.commit()

            self.assertEqual(db.query(StatementRow).count(), 4)
            self.assertEqual(db.query(Transaction).count(), 3)

            overlap_transactions = (
                db.query(Transaction).filter(Transaction.bank_reference_id == "PAY-500").all()
            )
            self.assertEqual(len(overlap_transactions), 1)
            self.assertEqual(len(overlap_transactions[0].statement_rows), 2)

    def test_fuzzy_overlap_dedupe_handles_text_variance(self) -> None:
        with self._Session() as db:
            self._create_import_with_bundles(
                db,
                name="text-var-a",
                bundle_ids=["st_ozon_text_var_a"],
            )
            db.commit()

            self._create_import_with_bundles(
                db,
                name="text-var-b",
                bundle_ids=["st_ozon_text_var_b"],
            )
            db.commit()

            self.assertEqual(db.query(StatementRow).count(), 2)
            self.assertEqual(db.query(Transaction).count(), 1)

            transactions = (
                db.query(Transaction).filter(Transaction.merchant_normalized == "Yandex Go").all()
            )
            self.assertEqual(len(transactions), 1)
            self.assertEqual(len(transactions[0].statement_rows), 2)

    def test_fuzzy_overlap_dedupe_handles_posting_operation_date_drift(self) -> None:
        with self._Session() as db:
            self._create_import_with_bundles(
                db,
                name="date-drift-a",
                bundle_ids=["st_ozon_date_drift_a"],
            )
            db.commit()

            self._create_import_with_bundles(
                db,
                name="date-drift-b",
                bundle_ids=["st_ozon_date_drift_b"],
            )
            db.commit()

            self.assertEqual(db.query(StatementRow).count(), 2)
            self.assertEqual(db.query(Transaction).count(), 1)

            transactions = (
                db.query(Transaction).filter(Transaction.merchant_normalized == "StreamCo").all()
            )
            self.assertEqual(len(transactions), 1)
            self.assertEqual(len(transactions[0].statement_rows), 2)

    def test_fuzzy_overlap_dedupe_does_not_merge_distinct_same_amount_transactions(self) -> None:
        with self._Session() as db:
            self._create_import_with_bundles(
                db,
                name="same-amount-a",
                bundle_ids=["st_ozon_same_amount_a"],
            )
            db.commit()

            self._create_import_with_bundles(
                db,
                name="same-amount-b",
                bundle_ids=["st_ozon_same_amount_b"],
            )
            db.commit()

            self.assertEqual(db.query(StatementRow).count(), 3)
            self.assertEqual(db.query(Transaction).count(), 2)

            coffee_txs = (
                db.query(Transaction)
                .filter(Transaction.merchant_normalized == "Coffee House")
                .order_by(Transaction.operation_datetime.asc())
                .all()
            )
            self.assertEqual(len(coffee_txs), 2)
            self.assertEqual(len(coffee_txs[0].statement_rows), 2)
            self.assertEqual(len(coffee_txs[1].statement_rows), 1)

    def test_ambiguous_fuzzy_overlap_dedupe_creates_overlap_exception(self) -> None:
        with self._Session() as db:
            self._create_import_with_bundles(
                db,
                name="ambiguous-a",
                bundle_ids=["st_ozon_text_var_a"],
            )
            db.commit()

            existing = (
                db.query(Transaction).filter(Transaction.merchant_normalized == "Yandex Go").one()
            )
            db.add(
                Transaction(
                    account_id=existing.account_id,
                    dedup_key="manual-ambiguous-yandex-go",
                    operation_datetime=existing.operation_datetime,
                    posting_datetime=existing.posting_datetime,
                    amount=existing.amount,
                    currency=existing.currency,
                    direction=existing.direction,
                    description_raw="Yandex Go ride",
                    merchant_normalized=existing.merchant_normalized,
                    bank_reference_id="",
                    bank_category=existing.bank_category,
                    meaning=existing.meaning,
                    meaning_confidence=existing.meaning_confidence,
                    category=existing.category,
                    tags=existing.tags,
                    review_status="reviewed",
                    timestamp_precision=existing.timestamp_precision,
                )
            )
            db.commit()

            self._create_import_with_bundles(
                db,
                name="ambiguous-b",
                bundle_ids=["st_ozon_text_var_b"],
            )
            db.commit()

            self.assertEqual(db.query(StatementRow).count(), 2)
            self.assertEqual(db.query(Transaction).count(), 3)
            self.assertEqual(
                db.query(ExceptionItem)
                .filter(ExceptionItem.exception_type == "suspected_overlap_duplicate")
                .count(),
                1,
            )

    def test_confirm_reject_persistence_survives_incremental_detect(self) -> None:
        with self._Session() as db:
            self._create_import_with_bundles(
                db,
                name="full",
                bundle_ids=["st_ozon_main", "st_ozon_overlap", "st_sber_main", "st_yandex_main"],
            )
            detect_transfer_links_in_session(db)
            db.commit()

            suggested = db.query(TransferLink).filter(TransferLink.status == "suggested").first()
            assert suggested is not None

            confirm_transfer_link(db, link=suggested)
            db.commit()
            detect_transfer_links_in_session(db)
            db.commit()

            refreshed = db.query(TransferLink).filter(TransferLink.id == suggested.id).first()
            assert refreshed is not None
            self.assertEqual(refreshed.status, "confirmed")

            reject_transfer_link(db, link=refreshed)
            db.commit()
            detect_transfer_links_in_session(db)
            db.commit()

            pair_links = (
                db.query(TransferLink)
                .filter(TransferLink.transaction_out_id == refreshed.transaction_out_id)
                .filter(TransferLink.transaction_in_id == refreshed.transaction_in_id)
                .all()
            )
            self.assertEqual(len(pair_links), 1)
            self.assertEqual(pair_links[0].status, "rejected")

    def test_delete_statement_triggers_transfer_reevaluation(self) -> None:
        with self._Session() as db:
            self._create_import_with_bundles(
                db,
                name="full",
                bundle_ids=["st_ozon_main", "st_ozon_overlap", "st_sber_main", "st_yandex_main"],
            )
            _, fallback_tx = self._add_fallback_transfer_out(db)

            detect_transfer_links_in_session(db)
            db.commit()

            sber_in = (
                db.query(Transaction).filter(Transaction.bank_reference_id == "XFER-101").first()
            )
            assert sber_in is not None
            self.assertFalse(
                db.query(TransferLink)
                .filter(TransferLink.transaction_out_id == fallback_tx.id)
                .filter(TransferLink.transaction_in_id == sber_in.id)
                .count()
            )

            ozon_main = (
                db.query(Statement)
                .filter(Statement.account_display == "acc_ozon")
                .filter(Statement.period_start == datetime(2026, 1, 1, 0, 0, 0))
                .first()
            )
            assert ozon_main is not None

            delete_statement(ozon_main.id, db=db)

            reevaluated = (
                db.query(TransferLink)
                .filter(TransferLink.transaction_out_id == fallback_tx.id)
                .filter(TransferLink.transaction_in_id == sber_in.id)
                .first()
            )
            self.assertIsNotNone(reevaluated)
            assert reevaluated is not None
            self.assertEqual(reevaluated.status, "suggested")

    def test_delete_batch_triggers_transfer_reevaluation(self) -> None:
        with self._Session() as db:
            batch_primary, _ = self._create_import_with_bundles(
                db,
                name="primary",
                bundle_ids=["st_ozon_main"],
            )
            self._create_import_with_bundles(
                db,
                name="secondary",
                bundle_ids=["st_sber_main", "st_yandex_main"],
            )
            _, fallback_tx = self._add_fallback_transfer_out(db)

            detect_transfer_links_in_session(db)
            db.commit()

            sber_in = (
                db.query(Transaction).filter(Transaction.bank_reference_id == "XFER-101").first()
            )
            assert sber_in is not None

            self.assertFalse(
                db.query(TransferLink)
                .filter(TransferLink.transaction_out_id == fallback_tx.id)
                .filter(TransferLink.transaction_in_id == sber_in.id)
                .count()
            )

            delete_import_batch(batch_primary.id, db=db)

            reevaluated = (
                db.query(TransferLink)
                .filter(TransferLink.transaction_out_id == fallback_tx.id)
                .filter(TransferLink.transaction_in_id == sber_in.id)
                .first()
            )
            self.assertIsNotNone(reevaluated)
            assert reevaluated is not None
            self.assertEqual(reevaluated.status, "suggested")


if __name__ == "__main__":
    unittest.main()
