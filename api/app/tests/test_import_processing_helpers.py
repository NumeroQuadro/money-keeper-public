from __future__ import annotations

import unittest
from datetime import datetime
from decimal import Decimal
from unittest.mock import patch

from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.domain import TxCandidate
from app.models import Account, Transaction
from app.services import import_processing
from app.tests.db_test_utils import get_test_engine


class ImportProcessingHelperTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = get_test_engine()
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)
        self._Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    def test_iter_chunks_splits_input(self) -> None:
        chunks = list(import_processing._iter_chunks(["a", "b", "c", "d", "e"], 2))
        self.assertEqual(chunks, [["a", "b"], ["c", "d"], ["e"]])

    def test_sanitize_import_error_truncates(self) -> None:
        message = "x" * (import_processing.MAX_IMPORT_ERROR_MESSAGE_LEN + 40)
        sanitized = import_processing._sanitize_import_error(message)
        self.assertTrue(sanitized.endswith("…"))
        self.assertLessEqual(len(sanitized), import_processing.MAX_IMPORT_ERROR_MESSAGE_LEN)

    def test_load_existing_transactions_uses_chunked_lookup(self) -> None:
        with self._Session() as db:
            account = Account(id="acc-a", provider="ozon", account_type="card")
            other_account = Account(id="acc-b", provider="sber", account_type="card")
            db.add_all([account, other_account])
            db.flush()

            db.add_all(
                [
                    Transaction(account_id="acc-a", dedup_key="k1", amount=1, direction="out"),
                    Transaction(account_id="acc-a", dedup_key="k2", amount=2, direction="out"),
                    Transaction(account_id="acc-a", dedup_key="k3", amount=3, direction="out"),
                    Transaction(account_id="acc-b", dedup_key="k1", amount=9, direction="out"),
                ]
            )
            db.commit()

            with patch.object(import_processing, "DEDUPE_LOOKUP_CHUNK_SIZE", 2):
                found = import_processing._load_existing_transactions_by_dedup_key(
                    db,
                    account_id="acc-a",
                    candidate_keys=["k1", "k2", "k3", "missing"],
                )

        self.assertEqual(sorted(tx.dedup_key for tx in found), ["k1", "k2", "k3"])

    def test_inject_synthetic_references_for_same_statement_duplicates(self) -> None:
        base = TxCandidate(
            account_id="acc-1",
            statement_row_id="sr-1",
            operation_datetime=datetime(2026, 1, 1, 10, 0, 0),
            posting_datetime=None,
            amount=Decimal("100.00"),
            currency="RUB",
            direction="out",
            description_raw="Перевод СБП 100,00",
            merchant_normalized="",
            bank_reference_id="",
            bank_category="transfer",
            meaning="unknown",
            meaning_confidence=Decimal("0.50"),
            category="",
            tags=[],
            review_status="needs_review",
            raw_text="01.01.2026 10:00 Перевод СБП 100,00",
        )
        duplicate = TxCandidate(
            account_id="acc-1",
            statement_row_id="sr-2",
            operation_datetime=base.operation_datetime,
            posting_datetime=base.posting_datetime,
            amount=base.amount,
            currency=base.currency,
            direction=base.direction,
            description_raw=base.description_raw,
            merchant_normalized="",
            bank_reference_id="",
            bank_category=base.bank_category,
            meaning=base.meaning,
            meaning_confidence=base.meaning_confidence,
            category="",
            tags=[],
            review_status="needs_review",
            raw_text=base.raw_text,
        )
        already_referenced = TxCandidate(
            account_id="acc-1",
            statement_row_id="sr-3",
            operation_datetime=base.operation_datetime,
            posting_datetime=None,
            amount=Decimal("250.00"),
            currency="RUB",
            direction="out",
            description_raw="Перевод СБП 250,00",
            merchant_normalized="",
            bank_reference_id="REF-250",
            bank_category="transfer",
            meaning="unknown",
            meaning_confidence=Decimal("0.50"),
            category="",
            tags=[],
            review_status="needs_review",
            raw_text="01.01.2026 10:05 Перевод СБП 250,00",
        )

        first_pass = import_processing._inject_synthetic_references_for_statement_duplicates(
            [base, duplicate, already_referenced],
            statement_id="st-1",
        )
        second_pass = import_processing._inject_synthetic_references_for_statement_duplicates(
            [base, duplicate, already_referenced],
            statement_id="st-1",
        )

        refs = [candidate.bank_reference_id for candidate in first_pass]
        self.assertEqual(refs[2], "REF-250")
        self.assertNotEqual(refs[0], "")
        self.assertNotEqual(refs[1], "")
        self.assertNotEqual(refs[0], refs[1])
        self.assertEqual(len(refs[0]), 24)
        self.assertEqual(len(refs[1]), 24)
        self.assertEqual(
            [candidate.bank_reference_id for candidate in second_pass],
            refs,
        )


if __name__ == "__main__":
    unittest.main()
