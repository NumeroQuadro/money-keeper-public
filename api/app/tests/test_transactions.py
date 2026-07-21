from __future__ import annotations

import unittest
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy.orm import sessionmaker

from app.api.transactions import (
    approve_transaction_category,
    list_transactions,
    mark_transaction_duplicate,
    mark_transaction_reviewed,
)
from app.db import Base
from app.models import Account, Transaction
from app.tests.db_test_utils import get_test_engine


class TransactionFilterTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = get_test_engine()
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)
        self._Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    def test_tags_filter_matches_any_tag(self) -> None:
        with self._Session() as db:
            tx1 = Transaction(
                amount=10,
                currency="RUB",
                direction="out",
                operation_datetime=datetime(2026, 1, 2, 10, 0, 0),
                description_raw="Coffee",
                tags=["food", "coffee"],
            )
            tx2 = Transaction(
                amount=20,
                currency="RUB",
                direction="out",
                operation_datetime=datetime(2026, 1, 3, 9, 0, 0),
                description_raw="Rent",
                tags=["rent"],
            )
            tx3 = Transaction(
                amount=30,
                currency="RUB",
                direction="out",
                operation_datetime=datetime(2026, 1, 4, 8, 0, 0),
                description_raw="Misc",
                tags=None,
            )
            tx4 = Transaction(
                amount=15,
                currency="RUB",
                direction="out",
                operation_datetime=datetime(2026, 1, 4, 9, 30, 0),
                description_raw="Tool rental",
                tags=["rental"],
            )
            db.add_all([tx1, tx2, tx3, tx4])
            db.commit()

            result = list_transactions(tags="coffee", db=db)
            ids = {item.id for item in result["items"]}
            self.assertEqual(result["total"], 1)
            self.assertEqual(ids, {tx1.id})

            result_any = list_transactions(tags="rent,coffee", db=db)
            ids_any = {item.id for item in result_any["items"]}
            self.assertEqual(result_any["total"], 2)
            self.assertEqual(ids_any, {tx1.id, tx2.id})

            exact_match = list_transactions(tags="rent", db=db)
            exact_ids = {item.id for item in exact_match["items"]}
            self.assertEqual(exact_match["total"], 1)
            self.assertEqual(exact_ids, {tx2.id})

    def test_account_filter_limits_results_to_selected_account(self) -> None:
        with self._Session() as db:
            account_a = Account(provider="ozon", account_type="card", display_name="Main")
            account_b = Account(provider="sber", account_type="card", display_name="Reserve")
            db.add_all([account_a, account_b])
            db.flush()

            tx1 = Transaction(
                account_id=account_a.id,
                amount=100,
                currency="RUB",
                direction="out",
                operation_datetime=datetime(2026, 1, 2, 10, 0, 0),
                description_raw="Store A",
            )
            tx2 = Transaction(
                account_id=account_b.id,
                amount=200,
                currency="RUB",
                direction="out",
                operation_datetime=datetime(2026, 1, 3, 10, 0, 0),
                description_raw="Store B",
            )
            db.add_all([tx1, tx2])
            db.commit()

            result = list_transactions(account_id=account_a.id, db=db)
            ids = {item.id for item in result["items"]}
            self.assertEqual(result["total"], 1)
            self.assertEqual(ids, {tx1.id})

    def test_default_filter_excludes_internal_transfers_only(self) -> None:
        with self._Session() as db:
            tx_visible = Transaction(
                amount=50,
                currency="RUB",
                direction="out",
                operation_datetime=datetime(2026, 1, 5, 9, 0, 0),
                description_raw="Lunch",
                meaning="unknown",
            )
            tx_internal = Transaction(
                amount=50,
                currency="RUB",
                direction="out",
                operation_datetime=datetime(2026, 1, 5, 10, 0, 0),
                description_raw="Own transfer",
                meaning="internal_transfer",
            )
            tx_external = Transaction(
                amount=25,
                currency="RUB",
                direction="out",
                operation_datetime=datetime(2026, 1, 5, 11, 0, 0),
                description_raw="Transfer to another person",
                meaning="external_transfer",
            )
            tx_transfer_hinted = Transaction(
                amount=15,
                currency="RUB",
                direction="out",
                operation_datetime=datetime(2026, 1, 5, 12, 0, 0),
                description_raw="Перевод по СБП другу",
                meaning="unknown",
            )
            tx_high_conf_transfer = Transaction(
                amount=200,
                currency="RUB",
                direction="out",
                operation_datetime=datetime(2026, 1, 5, 12, 30, 0),
                description_raw="Перевод с карты 200,00 Перевод для И. Иван Иванович",
                bank_category="transfer",
                bank_reference_id="SBP123456",
                meaning="unknown",
            )
            tx_high_conf_inflow_below_threshold = Transaction(
                amount=105,
                currency="RUB",
                direction="in",
                operation_datetime=datetime(2026, 1, 5, 12, 35, 0),
                description_raw="Перевод сбп +105,00 Перевод от И. Иван Иванович",
                bank_category="transfer",
                bank_reference_id="SBP777777",
                meaning="unknown",
            )
            tx_small_transfer = Transaction(
                amount=50,
                currency="RUB",
                direction="out",
                operation_datetime=datetime(2026, 1, 5, 12, 40, 0),
                description_raw="Перевод с карты 50,00 Перевод для И. Иван Иванович",
                bank_category="transfer",
                bank_reference_id="SBP654321",
                meaning="unknown",
            )
            db.add_all(
                [
                    tx_visible,
                    tx_internal,
                    tx_external,
                    tx_transfer_hinted,
                    tx_high_conf_transfer,
                    tx_high_conf_inflow_below_threshold,
                    tx_small_transfer,
                ]
            )
            db.commit()

            result = list_transactions(db=db)
            ids = {item.id for item in result["items"]}
            self.assertEqual(result["total"], 6)
            self.assertEqual(
                ids,
                {
                    tx_visible.id,
                    tx_external.id,
                    tx_transfer_hinted.id,
                    tx_high_conf_transfer.id,
                    tx_high_conf_inflow_below_threshold.id,
                    tx_small_transfer.id,
                },
            )

            high_conf_result = list_transactions(
                cashflow_lens="high_confidence_transfer_like",
                db=db,
            )
            high_conf_ids = {item.id for item in high_conf_result["items"]}
            self.assertEqual(high_conf_result["total"], 3)
            self.assertEqual(
                high_conf_ids,
                {
                    tx_visible.id,
                    tx_transfer_hinted.id,
                    tx_high_conf_inflow_below_threshold.id,
                },
            )

            strict_result = list_transactions(cashflow_lens="strict_transfer_like", db=db)
            strict_ids = {item.id for item in strict_result["items"]}
            self.assertEqual(strict_result["total"], 1)
            self.assertEqual(strict_ids, {tx_visible.id})

            include_result = list_transactions(
                include_transfers=True,
                cashflow_lens="strict_transfer_like",
                db=db,
            )
            include_ids = {item.id for item in include_result["items"]}
            self.assertEqual(include_result["total"], 7)
            self.assertEqual(
                include_ids,
                {
                    tx_visible.id,
                    tx_internal.id,
                    tx_external.id,
                    tx_transfer_hinted.id,
                    tx_high_conf_transfer.id,
                    tx_high_conf_inflow_below_threshold.id,
                    tx_small_transfer.id,
                },
            )

    def test_list_ordering_uses_robust_event_datetime_and_date_only_tiebreakers(self) -> None:
        with self._Session() as db:
            tx_posting_only = Transaction(
                amount=100,
                currency="RUB",
                direction="out",
                operation_datetime=None,
                posting_datetime=datetime(2026, 1, 5, 9, 30, 0),
                timestamp_precision="unknown",
                description_raw="posting only",
            )
            tx_date_only_b = Transaction(
                amount=50,
                currency="RUB",
                direction="out",
                operation_datetime=datetime(2026, 1, 5, 0, 0, 0),
                posting_datetime=None,
                timestamp_precision="date_only",
                source_statement_id="st-b",
                source_page_number=1,
                source_row_index=2,
                description_raw="date only b",
            )
            tx_date_only_a = Transaction(
                amount=40,
                currency="RUB",
                direction="out",
                operation_datetime=datetime(2026, 1, 5, 0, 0, 0),
                posting_datetime=None,
                timestamp_precision="date_only",
                source_statement_id="st-a",
                source_page_number=3,
                source_row_index=10,
                description_raw="date only a",
            )
            db.add_all([tx_date_only_a, tx_posting_only, tx_date_only_b])
            db.commit()

            result = list_transactions(db=db)
            ordered_ids = [item.id for item in result["items"]]
            self.assertEqual(
                ordered_ids,
                [tx_posting_only.id, tx_date_only_b.id, tx_date_only_a.id],
            )

    def test_review_status_filter_limits_results_to_requested_review_state(self) -> None:
        with self._Session() as db:
            tx_review = Transaction(
                amount=10,
                currency="RUB",
                direction="out",
                operation_datetime=datetime(2026, 1, 8, 10, 0, 0),
                description_raw="Needs review",
                review_status="needs_review",
            )
            tx_done = Transaction(
                amount=20,
                currency="RUB",
                direction="out",
                operation_datetime=datetime(2026, 1, 8, 11, 0, 0),
                description_raw="Reviewed",
                review_status="reviewed",
            )
            db.add_all([tx_review, tx_done])
            db.commit()

            result = list_transactions(review_status="needs_review", db=db)
            ids = {item.id for item in result["items"]}
            self.assertEqual(result["total"], 1)
            self.assertEqual(ids, {tx_review.id})

    def test_needs_human_review_filter_skips_obvious_merchant_transaction(self) -> None:
        with self._Session() as db:
            obvious = Transaction(
                amount=890,
                currency="RUB",
                direction="out",
                operation_datetime=datetime(2026, 1, 8, 12, 0, 0),
                description_raw="Samokat order",
                merchant_normalized="Samokat",
                bank_category="shopping",
                meaning="spend",
                category="",
                review_status="reviewed",
            )
            ambiguous = Transaction(
                amount=890,
                currency="RUB",
                direction="out",
                operation_datetime=datetime(2026, 1, 8, 12, 5, 0),
                description_raw="Unknown debit",
                merchant_normalized="",
                bank_category="",
                meaning="unknown",
                category="",
                review_status="reviewed",
            )
            db.add_all([obvious, ambiguous])
            db.commit()

            result = list_transactions(needs_human_review=True, include_transfers=True, db=db)
            ids = {item.id for item in result["items"]}

            self.assertEqual(result["total"], 1)
            self.assertEqual(ids, {ambiguous.id})
            self.assertEqual(result["items"][0].review_reasons, ["uncategorized_needs_review"])
            self.assertTrue(result["items"][0].needs_human_review)

    def test_needs_human_review_filter_skips_obvious_categorized_inflow(self) -> None:
        with self._Session() as db:
            inflow = Transaction(
                amount=25000,
                currency="RUB",
                direction="in",
                operation_datetime=datetime(2026, 1, 8, 13, 0, 0),
                description_raw="Salary",
                merchant_normalized="Employer",
                bank_category="income",
                meaning="income",
                category="Salary",
                review_status="reviewed",
            )
            ambiguous = Transaction(
                amount=25000,
                currency="RUB",
                direction="in",
                operation_datetime=datetime(2026, 1, 8, 13, 5, 0),
                description_raw="Incoming transfer",
                merchant_normalized="",
                bank_category="",
                meaning="unknown",
                category="",
                review_status="reviewed",
            )
            db.add_all([inflow, ambiguous])
            db.commit()

            result = list_transactions(needs_human_review=True, include_transfers=True, db=db)
            ids = {item.id for item in result["items"]}

            self.assertEqual(result["total"], 1)
            self.assertEqual(ids, {ambiguous.id})
            self.assertEqual(result["items"][0].review_reasons, ["uncategorized_needs_review"])
            self.assertTrue(result["items"][0].needs_human_review)

    def test_category_empty_filter_treats_blank_and_null_categories_as_empty(self) -> None:
        with self._Session() as db:
            tx_null = Transaction(
                amount=10,
                currency="RUB",
                direction="out",
                operation_datetime=datetime(2026, 1, 9, 10, 0, 0),
                description_raw="Null category",
                category=None,
            )
            tx_blank = Transaction(
                amount=20,
                currency="RUB",
                direction="out",
                operation_datetime=datetime(2026, 1, 9, 11, 0, 0),
                description_raw="Blank category",
                category="   ",
            )
            tx_filled = Transaction(
                amount=30,
                currency="RUB",
                direction="out",
                operation_datetime=datetime(2026, 1, 9, 12, 0, 0),
                description_raw="Filled category",
                category="Food",
            )
            db.add_all([tx_null, tx_blank, tx_filled])
            db.commit()

            empty_result = list_transactions(category_empty=True, db=db)
            empty_ids = {item.id for item in empty_result["items"]}
            self.assertEqual(empty_result["total"], 2)
            self.assertEqual(empty_ids, {tx_null.id, tx_blank.id})

            filled_result = list_transactions(category_empty=False, db=db)
            filled_ids = {item.id for item in filled_result["items"]}
            self.assertEqual(filled_result["total"], 1)
            self.assertEqual(filled_ids, {tx_filled.id})

    def test_approve_transaction_category_uses_existing_or_bank_category_and_marks_reviewed(
        self,
    ) -> None:
        with self._Session() as db:
            tx = Transaction(
                amount=15,
                currency="RUB",
                direction="out",
                operation_datetime=datetime(2026, 1, 10, 10, 0, 0),
                description_raw="Cafe",
                bank_category="food",
                category="",
                review_status="needs_review",
            )
            db.add(tx)
            db.commit()

            result = approve_transaction_category(tx.id, db=db)

            self.assertEqual(result.category, "Food")
            self.assertEqual(result.review_status, "reviewed")

    def test_mark_transaction_reviewed_updates_only_review_status(self) -> None:
        with self._Session() as db:
            tx = Transaction(
                amount=15,
                currency="RUB",
                direction="out",
                operation_datetime=datetime(2026, 1, 10, 10, 0, 0),
                description_raw="Cafe",
                category="Groceries",
                review_status="needs_review",
            )
            db.add(tx)
            db.commit()

            result = mark_transaction_reviewed(tx.id, db=db)

            self.assertEqual(result.review_status, "reviewed")
            self.assertEqual(result.category, "Groceries")

    def test_mark_transaction_duplicate_adds_tag_and_fills_duplicate_category_when_empty(
        self,
    ) -> None:
        with self._Session() as db:
            tx = Transaction(
                amount=15,
                currency="RUB",
                direction="out",
                operation_datetime=datetime(2026, 1, 10, 10, 0, 0),
                description_raw="Duplicate",
                category="",
                tags=["manual"],
                review_status="needs_review",
            )
            db.add(tx)
            db.commit()

            result = mark_transaction_duplicate(tx.id, db=db)

            self.assertEqual(result.review_status, "reviewed")
            self.assertEqual(result.category, "Duplicate")
            self.assertEqual(result.tags, ["manual", "duplicate"])

    def test_transaction_review_actions_raise_404_for_unknown_transaction(self) -> None:
        with self._Session() as db:
            with self.assertRaises(HTTPException) as approved:
                approve_transaction_category("missing", db=db)
            self.assertEqual(approved.exception.status_code, 404)

            with self.assertRaises(HTTPException) as reviewed:
                mark_transaction_reviewed("missing", db=db)
            self.assertEqual(reviewed.exception.status_code, 404)

            with self.assertRaises(HTTPException) as duplicated:
                mark_transaction_duplicate("missing", db=db)
            self.assertEqual(duplicated.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
