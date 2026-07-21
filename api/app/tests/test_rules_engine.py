from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from sqlalchemy.orm import sessionmaker

from app.api.rules import list_rules
from app.db import Base
from app.models import Rule, Transaction
from app.services.rules_engine import (
    apply_rules_to_transactions,
    apply_rules_in_session,
    bootstrap_default_rules_if_needed,
    preview_rule_application_in_session,
)
from app.tests.db_test_utils import get_test_engine


class RuleEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = get_test_engine()
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)
        self._Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    def test_category_uses_highest_priority_matching_rule(self) -> None:
        with self._Session() as db:
            db.add(
                Transaction(
                    amount=5,
                    currency="RUB",
                    direction="out",
                    operation_datetime=datetime(2026, 1, 1, 12, 0, 0),
                    description_raw="Coffee shop",
                    category="",
                )
            )
            db.add(
                Rule(
                    name="Coffee (food)",
                    pattern="coffee",
                    priority=10,
                    enabled=True,
                    actions={"set_category": "Food"},
                )
            )
            db.add(
                Rule(
                    name="Coffee (shopping)",
                    pattern="coffee",
                    priority=20,
                    enabled=True,
                    actions={"set_category": "Shopping"},
                )
            )
            db.commit()

            result = apply_rules_in_session(db)
            db.commit()

            tx = db.query(Transaction).first()
            assert tx is not None
            self.assertEqual(tx.category, "Food")
            self.assertEqual(result.transactions_updated, 1)

    def test_tags_set_add_remove_are_deterministic(self) -> None:
        with self._Session() as db:
            tx = Transaction(
                amount=100,
                currency="RUB",
                direction="out",
                operation_datetime=datetime(2026, 1, 2, 12, 0, 0),
                description_raw="UBER ride",
            )
            db.add(tx)
            db.add(
                Rule(
                    name="Uber base tags",
                    pattern="uber",
                    priority=5,
                    enabled=True,
                    actions={"set_tags": ["transport"], "add_tags": ["ride"]},
                )
            )
            db.add(
                Rule(
                    name="Uber refine tags",
                    pattern="uber",
                    priority=10,
                    enabled=True,
                    actions={"add_tags": ["ride", "late"], "remove_tags": ["transport"]},
                )
            )
            db.commit()

            apply_rules_in_session(db)
            db.commit()

            refreshed = db.query(Transaction).filter(Transaction.id == tx.id).first()
            assert refreshed is not None
            self.assertEqual(refreshed.tags, ["ride", "late"])

    def test_preview_and_apply_counts_are_idempotent(self) -> None:
        with self._Session() as db:
            db.add_all(
                [
                    Transaction(
                        amount=10,
                        currency="RUB",
                        direction="out",
                        operation_datetime=datetime(2026, 1, 3, 12, 0, 0),
                        description_raw="Coffee beans",
                    ),
                    Transaction(
                        amount=20,
                        currency="RUB",
                        direction="out",
                        operation_datetime=datetime(2026, 1, 3, 12, 5, 0),
                        description_raw="Tea",
                    ),
                    Transaction(
                        amount=5,
                        currency="RUB",
                        direction="out",
                        operation_datetime=datetime(2026, 1, 4, 9, 0, 0),
                        description_raw="coffee shop",
                    ),
                ]
            )
            db.add(
                Rule(
                    name="Coffee category",
                    pattern="coffee",
                    priority=1,
                    enabled=True,
                    actions={"set_category": "Food"},
                )
            )
            db.commit()

            preview = preview_rule_application_in_session(db)
            self.assertEqual(preview.transactions_scanned, 3)
            self.assertEqual(preview.transactions_matched, 2)
            self.assertEqual(preview.transactions_changed, 2)
            self.assertEqual(preview.transactions_updated, 0)

            first = apply_rules_in_session(db)
            db.commit()
            self.assertEqual(first.transactions_updated, 2)

            second = apply_rules_in_session(db)
            db.commit()
            self.assertEqual(second.transactions_updated, 0)

    def test_bootstrap_default_rules_seed_and_apply_uncategorized(self) -> None:
        with self._Session() as db:
            tx_out = Transaction(
                amount=42,
                currency="RUB",
                direction="out",
                operation_datetime=datetime(2026, 1, 5, 12, 0, 0),
                description_raw="Lunch",
                category="",
                meaning="unknown",
            )
            tx_in = Transaction(
                amount=1000,
                currency="RUB",
                direction="in",
                operation_datetime=datetime(2026, 1, 5, 13, 0, 0),
                description_raw="Salary",
                category="",
                meaning="unknown",
            )
            tx_transfer = Transaction(
                amount=500,
                currency="RUB",
                direction="out",
                operation_datetime=datetime(2026, 1, 5, 14, 0, 0),
                description_raw="Own transfer",
                category="",
                meaning="internal_transfer",
            )
            tx_existing = Transaction(
                amount=20,
                currency="RUB",
                direction="out",
                operation_datetime=datetime(2026, 1, 5, 15, 0, 0),
                description_raw="Existing category",
                category="Food",
                meaning="unknown",
            )
            db.add_all([tx_out, tx_in, tx_transfer, tx_existing])
            db.commit()

            result = bootstrap_default_rules_if_needed(db)
            db.commit()

            self.assertEqual(result.default_rules_created, 3)
            self.assertEqual(result.transactions_updated, 3)

            refreshed_out = db.query(Transaction).filter(Transaction.id == tx_out.id).first()
            refreshed_in = db.query(Transaction).filter(Transaction.id == tx_in.id).first()
            refreshed_transfer = (
                db.query(Transaction).filter(Transaction.id == tx_transfer.id).first()
            )
            refreshed_existing = (
                db.query(Transaction).filter(Transaction.id == tx_existing.id).first()
            )
            assert refreshed_out is not None
            assert refreshed_in is not None
            assert refreshed_transfer is not None
            assert refreshed_existing is not None
            self.assertEqual(refreshed_out.category, "Spending")
            self.assertEqual(refreshed_in.category, "Income")
            self.assertEqual(refreshed_transfer.category, "Transfer")
            self.assertEqual(refreshed_existing.category, "Food")

            second = bootstrap_default_rules_if_needed(db)
            db.commit()
            self.assertEqual(second.default_rules_created, 0)
            self.assertEqual(second.transactions_updated, 0)

    def test_bootstrap_default_rules_does_not_run_when_custom_rules_exist(self) -> None:
        with self._Session() as db:
            db.add(
                Rule(
                    name="Custom existing rule",
                    pattern="coffee",
                    priority=10,
                    enabled=True,
                    actions={"set_category": "Food"},
                )
            )
            db.add(
                Transaction(
                    amount=5,
                    currency="RUB",
                    direction="out",
                    operation_datetime=datetime(2026, 1, 6, 10, 0, 0),
                    description_raw="Taxi",
                    category="",
                    meaning="unknown",
                )
            )
            db.commit()

            result = bootstrap_default_rules_if_needed(db)
            db.commit()

            self.assertEqual(result.default_rules_created, 0)
            self.assertEqual(result.transactions_updated, 0)

    def test_directional_guardrail_recategorizes_needs_review_rows(self) -> None:
        with self._Session() as db:
            tx_out = Transaction(
                amount=250,
                currency="RUB",
                direction="out",
                operation_datetime=datetime(2026, 1, 6, 12, 0, 0),
                description_raw="Legacy row with stale category",
                category="Income",
                meaning="unknown",
                review_status="needs_review",
            )
            tx_in = Transaction(
                amount=250,
                currency="RUB",
                direction="in",
                operation_datetime=datetime(2026, 1, 6, 13, 0, 0),
                description_raw="Legacy row with stale category",
                category="Spending",
                meaning="unknown",
                review_status="needs_review",
            )
            db.add_all([tx_out, tx_in])
            db.commit()

            result = apply_rules_in_session(db, include_transfers=True)
            db.commit()

            refreshed_out = db.query(Transaction).filter(Transaction.id == tx_out.id).first()
            refreshed_in = db.query(Transaction).filter(Transaction.id == tx_in.id).first()
            assert refreshed_out is not None
            assert refreshed_in is not None
            self.assertEqual(refreshed_out.category, "Spending")
            self.assertEqual(refreshed_in.category, "Income")
            self.assertEqual(result.transactions_updated, 2)

    def test_directional_guardrail_does_not_override_reviewed_rows(self) -> None:
        with self._Session() as db:
            tx = Transaction(
                amount=250,
                currency="RUB",
                direction="out",
                operation_datetime=datetime(2026, 1, 6, 14, 0, 0),
                description_raw="Reviewed correction",
                category="Income",
                meaning="unknown",
                review_status="reviewed",
            )
            db.add(tx)
            db.commit()

            result = apply_rules_in_session(db, include_transfers=True)
            db.commit()

            refreshed = db.query(Transaction).filter(Transaction.id == tx.id).first()
            assert refreshed is not None
            self.assertEqual(refreshed.category, "Income")
            self.assertEqual(result.transactions_updated, 0)

    def test_apply_rules_to_transactions_enforces_guardrail_without_rules(self) -> None:
        with self._Session() as db:
            tx_out = Transaction(
                amount=111,
                currency="RUB",
                direction="out",
                operation_datetime=datetime(2026, 1, 6, 15, 0, 0),
                description_raw="Import-time stale category",
                category="Income",
                meaning="unknown",
                review_status="needs_review",
            )
            tx_in = Transaction(
                amount=222,
                currency="RUB",
                direction="in",
                operation_datetime=datetime(2026, 1, 6, 15, 5, 0),
                description_raw="Import-time stale category",
                category="Spending",
                meaning="unknown",
                review_status="needs_review",
            )
            tx_reviewed = Transaction(
                amount=333,
                currency="RUB",
                direction="out",
                operation_datetime=datetime(2026, 1, 6, 15, 10, 0),
                description_raw="Reviewed explicit override",
                category="Income",
                meaning="unknown",
                review_status="reviewed",
            )
            db.add_all([tx_out, tx_in, tx_reviewed])
            db.commit()

            result = apply_rules_to_transactions(
                db,
                transactions=[tx_out, tx_in, tx_reviewed],
            )
            db.commit()

            refreshed_out = db.query(Transaction).filter(Transaction.id == tx_out.id).first()
            refreshed_in = db.query(Transaction).filter(Transaction.id == tx_in.id).first()
            refreshed_reviewed = (
                db.query(Transaction).filter(Transaction.id == tx_reviewed.id).first()
            )
            assert refreshed_out is not None
            assert refreshed_in is not None
            assert refreshed_reviewed is not None
            self.assertEqual(refreshed_out.category, "Spending")
            self.assertEqual(refreshed_in.category, "Income")
            self.assertEqual(refreshed_reviewed.category, "Income")
            self.assertEqual(result.transactions_scanned, 3)
            self.assertEqual(result.transactions_matched, 0)
            self.assertEqual(result.transactions_changed, 2)
            self.assertEqual(result.transactions_updated, 2)

    def test_list_rules_is_deterministic_for_priority_created_at_and_id(self) -> None:
        with self._Session() as db:
            base = datetime(2026, 1, 7, 12, 0, 0)
            db.add_all(
                [
                    Rule(
                        id="rule-b",
                        name="same-priority-created-at-b",
                        pattern="x",
                        priority=10,
                        enabled=True,
                        created_at=base,
                    ),
                    Rule(
                        id="rule-top-priority",
                        name="top-priority",
                        pattern="x",
                        priority=5,
                        enabled=True,
                        created_at=base + timedelta(minutes=5),
                    ),
                    Rule(
                        id="rule-a",
                        name="same-priority-created-at-a",
                        pattern="x",
                        priority=10,
                        enabled=True,
                        created_at=base,
                    ),
                    Rule(
                        id="rule-late-created",
                        name="same-priority-late-created",
                        pattern="x",
                        priority=10,
                        enabled=True,
                        created_at=base + timedelta(minutes=1),
                    ),
                ]
            )
            db.commit()

            ordered = list_rules(db=db)
            self.assertEqual(
                [rule.id for rule in ordered],
                ["rule-top-priority", "rule-a", "rule-b", "rule-late-created"],
            )

    def test_preview_sample_order_is_deterministic_when_timestamps_tie(self) -> None:
        with self._Session() as db:
            ts = datetime(2026, 1, 8, 9, 0, 0)
            db.add_all(
                [
                    Transaction(
                        id="tx-b",
                        amount=10,
                        currency="RUB",
                        direction="out",
                        operation_datetime=ts,
                        created_at=ts,
                        description_raw="Coffee first inserted",
                    ),
                    Transaction(
                        id="tx-a",
                        amount=10,
                        currency="RUB",
                        direction="out",
                        operation_datetime=ts,
                        created_at=ts,
                        description_raw="Coffee second inserted",
                    ),
                    Rule(
                        name="Coffee category",
                        pattern="coffee",
                        priority=1,
                        enabled=True,
                        actions={"set_category": "Food"},
                    ),
                ]
            )
            db.commit()

            preview = preview_rule_application_in_session(db, sample_limit=10)
            self.assertEqual([item.transaction_id for item in preview.sample], ["tx-a", "tx-b"])


if __name__ == "__main__":
    unittest.main()
