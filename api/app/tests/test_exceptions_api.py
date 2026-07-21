from __future__ import annotations

import unittest

from fastapi import HTTPException
from sqlalchemy.orm import sessionmaker

from app.api.exceptions import approve_exception_category, list_exceptions, mark_exception_duplicate
from app.db import Base
from app.models import ExceptionItem, Transaction
from app.tests.db_test_utils import get_test_engine


class ExceptionActionTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = get_test_engine()
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)
        self._Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    def test_approve_category_updates_transaction_and_resolves_exception(self) -> None:
        with self._Session() as db:
            tx = Transaction(
                amount=99,
                currency="RUB",
                direction="out",
                description_raw="Coffee shop",
                bank_category="food",
                category="",
                review_status="needs_review",
            )
            db.add(tx)
            db.flush()
            exc = ExceptionItem(
                exception_type="ambiguous_category",
                severity="medium",
                status="open",
                entity_type="transaction",
                entity_id=tx.id,
                rationale="Model confidence too low",
                payload={"suggested_category": "Food"},
            )
            db.add(exc)
            db.commit()

            result = approve_exception_category(exc.id, db=db)

            self.assertEqual(result.status, "resolved")
            self.assertEqual((result.payload or {}).get("resolution"), "category_approved")

            refreshed_tx = db.query(Transaction).filter(Transaction.id == tx.id).first()
            assert refreshed_tx is not None
            self.assertEqual(refreshed_tx.category, "Food")
            self.assertEqual(refreshed_tx.review_status, "reviewed")

    def test_mark_duplicate_updates_transaction_and_resolves_exception(self) -> None:
        with self._Session() as db:
            tx = Transaction(
                amount=10,
                currency="RUB",
                direction="out",
                description_raw="Duplicate item",
                category="",
                tags=["manual"],
                review_status="needs_review",
            )
            db.add(tx)
            db.flush()
            exc = ExceptionItem(
                exception_type="suspected_duplicate",
                severity="medium",
                status="open",
                entity_type="transaction",
                entity_id=tx.id,
                rationale="Looks duplicated across statements",
            )
            db.add(exc)
            db.commit()

            result = mark_exception_duplicate(exc.id, db=db)

            self.assertEqual(result.status, "resolved")
            self.assertEqual((result.payload or {}).get("resolution"), "marked_duplicate")

            refreshed_tx = db.query(Transaction).filter(Transaction.id == tx.id).first()
            assert refreshed_tx is not None
            self.assertEqual(refreshed_tx.category, "Duplicate")
            self.assertEqual(refreshed_tx.review_status, "reviewed")
            self.assertIn("duplicate", refreshed_tx.tags or [])

    def test_approve_category_requires_transaction_entity(self) -> None:
        with self._Session() as db:
            exc = ExceptionItem(
                exception_type="reconciliation_mismatch",
                severity="high",
                status="open",
                entity_type="statement",
                entity_id="st-1",
                rationale="Mismatch",
            )
            db.add(exc)
            db.commit()

            with self.assertRaises(HTTPException) as raised:
                approve_exception_category(exc.id, db=db)

            self.assertEqual(raised.exception.status_code, 400)

    def test_list_exceptions_can_filter_open_items(self) -> None:
        with self._Session() as db:
            open_exc = ExceptionItem(
                exception_type="ambiguous_category",
                severity="medium",
                status="open",
                rationale="Needs review",
            )
            resolved_exc = ExceptionItem(
                exception_type="suspected_duplicate",
                severity="low",
                status="resolved",
                rationale="Already handled",
            )
            db.add(open_exc)
            db.add(resolved_exc)
            db.commit()

            open_only = list_exceptions(status="open", db=db)

            self.assertEqual([item.id for item in open_only], [open_exc.id])


if __name__ == "__main__":
    unittest.main()
